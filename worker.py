#!/usr/bin/env python3
"""
==============================================================================
Niche Video Worker Orchestrator
==============================================================================
Autonomous video creation worker with full checkpointing, crash recovery,
and NVENC hardware-accelerated rendering. Modeled on the AffiliateKage design.
"""

import os
# Ensure MoviePy and imageio use system FFmpeg with NVENC hardware acceleration
os.environ["IMAGEIO_FFMPEG_EXE"] = "/usr/bin/ffmpeg"

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from loguru import logger

from app.config import config
from app.models.schema import VideoAspect, VideoConcatMode, VideoParams
from app.services import llm, material, subtitle, video, voice
from app.utils import utils
from core.checkpoint import CheckpointManager
from core.profile_loader import load_profile


def generate_fallback_visuals(task_dir: str, duration: float, aspect_ratio: str = "9:16", clip_duration: int = 4) -> list[str]:
    """Generate high-contrast cinematic atmospheric clips via NVENC if stock API keys are absent."""
    width, height = (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)
    palette = [
        ("0x0d1117", "0x161b22"),
        ("0x1a0f1a", "0x2d142c"),
        ("0x0b192c", "0x1e3e62"),
        ("0x1f1717", "0x2e073f"),
        ("0x0a0a0a", "0x1f2022"),
    ]
    clips = []
    needed_clips = max(1, math.ceil(duration / clip_duration))
    for i in range(needed_clips):
        c0, c1 = palette[i % len(palette)]
        clip_path = os.path.join(task_dir, f"procedural_clip_{i+1}.mp4")
        if not os.path.exists(clip_path):
            cmd = [
                utils.get_ffmpeg_binary(), "-y",
                "-f", "lavfi",
                "-i", f"gradients=s={width}x{height}:d={clip_duration}:c0={c0}:c1={c1}:speed=0.01",
                "-c:v", "h264_nvenc",
                "-pix_fmt", "yuv420p",
                clip_path
            ]
            subprocess.run(cmd, capture_output=True, check=True)
        clips.append(clip_path)
    return clips


def build_system_script_prompt(profile: dict, topic: str) -> str:
    persona = profile.get("persona", {})
    tone = persona.get("tone", "engaging, cinematic, dramatic")
    banned = persona.get("banned_phrases", [])
    banned_str = ", ".join(f'"{p}"' for p in banned) if banned else "None"

    return f"""# Role: Elite Short-Form Storyteller & Video Scriptwriter
# Niche: {profile['niche']['name']} ({profile['niche']['description']})
# Subject: {topic}

## Guidelines:
1. Tone: {tone}.
2. Pacing: Punchy, spoken-word cadence. Short sentences designed for maximum viewer retention.
3. Hook (First 3 seconds): Must start with an impossible fact, cognitive paradox, or high-stakes reveal.
4. Total Length: Between 60 and 110 words (approximately 30 to 45 seconds when spoken).
5. Strict Forbidden Phrases: Never use {banned_str}.
6. Structure: Return ONLY the raw script to be read aloud. No stage directions, no narrator labels, no markdown headers, no quotes.
"""


def generate_niche_script(profile: dict, topic: str) -> str:
    """Generate script adhering strictly to niche persona."""
    prompt = build_system_script_prompt(profile, topic)
    logger.info(f"Generating niche script for subject: '{topic}'...")
    response = llm._generate_response(prompt)
    if not response or response.startswith("Error:"):
        raise RuntimeError(f"Script generation failed: {response}")
    return response.strip()


def generate_visual_terms(script: str, profile: dict) -> list[str]:
    """Generate high-relevance visual search terms matching script beats."""
    prompt = f"""Given this video narration script, extract 3 to 6 high-quality, cinematic visual search terms (1-2 English words each) that can find matching B-roll footage.
Script:
"{script}"

Return ONLY a comma-separated list of visual terms (e.g. ancient ruins, golden sunset, stormy ocean). No explanations."""
    response = llm._generate_response(prompt)
    terms = [t.strip().strip('"').strip("'") for t in response.split(",") if t.strip()]
    if not terms:
        terms = [profile['niche']['name'].lower(), "cinematic mystery"]
    return terms[:6]


def run_worker_pipeline(profile_path: str, topic_override: str = None, clear_state: bool = False):
    profile = load_profile(profile_path)
    niche_slug = profile["niche"]["slug"]
    
    checkpoint_file = f"pipeline_state_{niche_slug}.json"
    checkpoint = CheckpointManager(checkpoint_file)

    if clear_state:
        print(f"[Worker: {niche_slug}] Clearing existing state for a fresh session.")
        checkpoint.clear()

    state = checkpoint.load()
    if state and state.get("stage") != "completed":
        print(f"============================================================")
        print(f" [Worker: {niche_slug}] Resuming from checkpoint: {state.get('stage')}")
        print(f"============================================================")
    else:
        task_id = str(uuid4())
        state = {
            "task_id": task_id,
            "niche": niche_slug,
            "stage": "start",
            "status": "running",
            "topic": topic_override or profile.get("series", {}).get("current_arc") or f"Secrets of {profile['niche']['name']}",
            "created_at": time.time(),
        }
        checkpoint.save(state)

    task_id = state["task_id"]
    task_dir = utils.task_dir(task_id)

    try:
        # -------------------------------------------------------------
        # STAGE 1: Topic & Script Generation
        # -------------------------------------------------------------
        if state["stage"] in {"start", "script_generating"}:
            print(f"\n[1/5] Writing script for '{state['topic']}'...")
            script = state.get("script")
            if not script:
                script = generate_niche_script(profile, state["topic"])
                terms = generate_visual_terms(script, profile)
                state.update({
                    "script": script,
                    "terms": terms,
                    "stage": "script_generated"
                })
                checkpoint.save(state)
            print(f"  ✓ Script ready ({len(script.split())} words)")
            print(f"  ✓ Visual terms: {', '.join(state['terms'])}")

        # -------------------------------------------------------------
        # STAGE 2: Voice Narration (Edge TTS or configured provider)
        # -------------------------------------------------------------
        if state["stage"] in {"script_generated", "audio_generating"}:
            print(f"\n[2/5] Synthesizing voiceover narration...")
            audio_file = os.path.join(task_dir, "audio.mp3")
            voice_config = profile.get("voice", {})
            voice_name = voice_config.get("voice_name", "en-US-ChristopherNeural")
            voice_rate = float(voice_config.get("voice_rate", 1.0))

            sub_maker = voice.tts(
                text=state["script"],
                voice_name=voice_name,
                voice_rate=voice_rate,
                voice_file=audio_file,
            )
            if not os.path.exists(audio_file):
                raise RuntimeError("Failed to synthesize narration audio.")

            duration = voice.get_audio_duration(audio_file)
            audio_duration = math.ceil(duration)
            if audio_duration <= 0:
                raise RuntimeError("Generated narration audio has 0s duration.")

            state.update({
                "audio_file": audio_file,
                "audio_duration": audio_duration,
                "stage": "audio_generated"
            })
            checkpoint.save(state)
            print(f"  ✓ Narration synthesized: {audio_duration}s ({audio_file})")

        # -------------------------------------------------------------
        # STAGE 3: Subtitles Alignment
        # -------------------------------------------------------------
        if state["stage"] in {"audio_generated", "subtitle_generating"}:
            print(f"\n[3/5] Generating word-aligned subtitles...")
            subtitle_path = os.path.join(task_dir, "subtitle.srt")
            
            # Re-generate sub_maker for exact cues
            sub_maker = voice.tts(
                text=state["script"],
                voice_name=profile["voice"].get("voice_name", "en-US-ChristopherNeural"),
                voice_rate=float(profile["voice"].get("voice_rate", 1.0)),
                voice_file=state["audio_file"],
            )
            voice.create_subtitle(text=state["script"], sub_maker=sub_maker, subtitle_file=subtitle_path)
            
            if not os.path.exists(subtitle_path):
                raise RuntimeError("Failed to generate subtitle track.")

            state.update({
                "subtitle_path": subtitle_path,
                "stage": "subtitle_generated"
            })
            checkpoint.save(state)
            print(f"  ✓ Subtitles ready ({subtitle_path})")

        # -------------------------------------------------------------
        # STAGE 4: Visual Materials Sourcing
        # -------------------------------------------------------------
        if state["stage"] in {"subtitle_generated", "materials_sourcing"}:
            print(f"\n[4/5] Preparing visual scene footage ({profile['visual']['source']})...")
            visual_cfg = profile.get("visual", {})
            source = visual_cfg.get("source", "pexels")
            aspect = VideoAspect(visual_cfg.get("aspect_ratio", "9:16"))
            clip_dur = int(visual_cfg.get("clip_duration", 3))

            downloaded_videos = []
            try:
                downloaded_videos = material.download_videos(
                    task_id=task_id,
                    search_terms=state["terms"],
                    source=source,
                    video_aspect=aspect,
                    video_concat_mode=VideoConcatMode.sequential,
                    audio_duration=state["audio_duration"],
                    max_clip_duration=clip_dur,
                    match_script_order=True,
                )
            except Exception as e:
                logger.warning(f"Stock material download skipped ({e}). Using cinematic atmospheric visuals.")

            if not downloaded_videos:
                print("  ℹ Generating cinematic atmospheric visuals via NVENC...")
                downloaded_videos = generate_fallback_visuals(
                    task_dir=task_dir,
                    duration=state["audio_duration"],
                    aspect_ratio=visual_cfg.get("aspect_ratio", "9:16"),
                    clip_duration=clip_dur,
                )

            state.update({
                "materials": downloaded_videos,
                "stage": "materials_ready"
            })
            checkpoint.save(state)
            print(f"  ✓ Acquired {len(downloaded_videos)} visual clips.")

        # -------------------------------------------------------------
        # STAGE 5: Hardware-Accelerated NVENC Compositing
        # -------------------------------------------------------------
        if state["stage"] in {"materials_ready", "video_rendering"}:
            print(f"\n[5/5] Hardware-rendering final video via NVENC on RTX 2070...")
            combined_video_path = os.path.join(task_dir, "combined.mp4")
            final_video_path = os.path.join(task_dir, "final.mp4")
            
            visual_cfg = profile.get("visual", {})
            aspect = VideoAspect(visual_cfg.get("aspect_ratio", "9:16"))
            
            # Step A: Combine clips to match duration
            video.combine_videos(
                combined_video_path=combined_video_path,
                video_paths=state["materials"],
                audio_file=state["audio_file"],
                video_aspect=aspect,
                video_concat_mode=VideoConcatMode.sequential,
                video_transition_mode=visual_cfg.get("transition_mode", "fade"),
                max_clip_duration=int(visual_cfg.get("clip_duration", 3)),
                threads=4,
                clip_speed=1.0,
            )

            # Step B: Burn subtitles and mix audio
            subtitle_cfg = profile.get("subtitle", {})
            params = VideoParams(
                video_subject=state.get("topic", "Short Video"),
                video_aspect=aspect,
                font_name="BeVietnamPro-Bold.ttf",
                font_size=subtitle_cfg.get("font_size", 52),
                text_fore_color=subtitle_cfg.get("color", "#FFFFFF"),
                stroke_color="#000000",
                stroke_width=subtitle_cfg.get("stroke_width", 3),
                subtitle_position=subtitle_cfg.get("position", "custom"),
                custom_position=float(subtitle_cfg.get("custom_position", 70.0)),
                bgm_type=profile.get("audio", {}).get("bgm_type", "random"),
                bgm_volume=float(profile.get("audio", {}).get("bgm_volume", 0.18)),
            )

            video.generate_video(
                video_path=combined_video_path,
                audio_path=state["audio_file"],
                subtitle_path=state["subtitle_path"],
                output_file=final_video_path,
                params=params,
            )

            if not os.path.exists(final_video_path) or os.path.getsize(final_video_path) == 0:
                raise RuntimeError("Video rendering failed or output file is 0 bytes.")

            state.update({
                "final_video": final_video_path,
                "stage": "video_rendered"
            })
            checkpoint.save(state)
            print(f"  ✓ Render complete: {final_video_path}")

        # -------------------------------------------------------------
        # STAGE 6: Archive & Metadata Assembly
        # -------------------------------------------------------------
        if state["stage"] == "video_rendered":
            out_dir = Path("output") / niche_slug / task_id
            out_dir.mkdir(parents=True, exist_ok=True)

            dest_mp4 = out_dir / f"{task_id}.mp4"
            shutil.copy2(state["final_video"], dest_mp4)

            # Build rich syndication metadata
            metadata = {
                "task_id": task_id,
                "niche": niche_slug,
                "topic": state["topic"],
                "script": state["script"],
                "duration_seconds": state["audio_duration"],
                "file_path": str(dest_mp4.resolve()),
                "file_size_mb": round(dest_mp4.stat().st_size / (1024 * 1024), 2),
                "visual_terms": state["terms"],
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["created_at"])),
                "completed_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            }

            meta_path = out_dir / "metadata.json"
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            state["stage"] = "completed"
            checkpoint.save(state)
            checkpoint.archive(str(Path("output") / niche_slug), task_id)

            # Clean worker status card
            print("\n============================================================")
            print(f" 🎬 VIDEO GENERATION COMPLETE [{niche_slug.upper()}]")
            print("============================================================")
            print(f"  • Subject:   {metadata['topic']}")
            print(f"  • Duration:  {metadata['duration_seconds']}s")
            print(f"  • Size:      {metadata['file_size_mb']} MB")
            print(f"  • Output:    {dest_mp4}")
            print(f"  • Metadata:  {meta_path}")
            print("============================================================\n")

    except KeyboardInterrupt:
        print(f"\n[Worker] Execution paused at stage '{state.get('stage')}'. Checkpoint saved.")
        state["status"] = "interrupted"
        checkpoint.save(state)
        sys.exit(0)

    except Exception as e:
        print(f"\n[Worker] Pipeline failed at stage '{state.get('stage')}': {e}")
        state["status"] = "failed"
        state["last_error"] = str(e)
        checkpoint.save(state)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Niche Video Worker")
    parser.add_argument("--profile", default="dark_history", help="Niche profile slug or path")
    parser.add_argument("--topic", default=None, help="Explicit topic override")
    parser.add_argument("--clear-state", action="store_true", help="Clear saved state and start fresh")
    args = parser.parse_args()

    run_worker_pipeline(
        profile_path=args.profile,
        topic_override=args.topic,
        clear_state=args.clear_state
    )
