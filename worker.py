#!/usr/bin/env python3
"""
==============================================================================
Niche Video Worker Orchestrator
==============================================================================
Autonomous video creation worker with full checkpointing, crash recovery,
and NVENC hardware-accelerated rendering. Modeled on the AffiliateKage design.
"""

import os
import socket

# Force IPv4 resolution to prevent SSLError / connection timeouts on hosts with unreachable IPv6
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _getaddrinfo_ipv4

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
from core.comfy_client import ComfyClient
from core.motion import image_to_cinematic_clip
from core.profile_loader import load_profile
from core.researcher import fetch_topic_research
from core.visual_fetcher import VisualFetcher


def build_system_script_prompt(profile: dict, topic: str, research_context: str = "") -> str:
    persona = profile.get("persona", {})
    tone = persona.get("tone", "suspenseful, investigative, documentary")
    banned = persona.get("banned_phrases", [])
    banned_str = ", ".join(f'"{p}"' for p in banned) if banned else "None"

    context_block = f"\n## Verified Historical Research & Facts:\n{research_context}\n" if research_context else ""

    return f"""# Role: Elite Short-Form Storyteller & Investigative Documentarian
# Niche: {profile['niche']['name']} ({profile['niche']['description']})
# Subject: {topic}
{context_block}
## Guidelines:
1. Tone: {tone}.
2. Pacing: Punchy, spoken-word cadence. Short sentences designed for maximum viewer retention.
3. Hook (First 3 seconds): Must start with an impossible fact, cognitive paradox, or high-stakes reveal grounded in the evidence.
4. Total Length: Between 65 and 95 words (approximately 30 to 45 seconds when spoken).
5. Grounding: Mention at least one specific artifact, date, or physical piece of evidence from the research.
6. Strict Forbidden Phrases: Never use {banned_str}.
7. Structure: Return ONLY the raw script to be read aloud. No stage directions, no narrator labels, no markdown headers, no quotes, and NEVER include word counts or bracketed notes (e.g. do NOT write "[Word count: 87]").
"""


def _get_llm_config(profile: dict) -> dict:
    app_cfg = dict(config.app)
    llm_cfg = profile.get("llm", {})
    provider = llm_cfg.get("provider", "ollama").lower()
    model = llm_cfg.get("model", "qwen3-coder-agent:latest")
    app_cfg["llm_provider"] = provider
    if provider == "ollama":
        app_cfg["ollama_model_name"] = model
        if not app_cfg.get("ollama_base_url"):
            app_cfg["ollama_base_url"] = "http://127.0.0.1:11434/v1"
    else:
        app_cfg[f"{provider}_model_name"] = model
    return app_cfg


def generate_niche_script(profile: dict, topic: str, research_context: str = "") -> str:
    """Generate script adhering strictly to niche persona and researched facts."""
    prompt = build_system_script_prompt(profile, topic, research_context)
    app_cfg = _get_llm_config(profile)
    model_name = app_cfg.get("ollama_model_name") or profile.get("llm", {}).get("model", "qwen3-coder-agent:latest")
    logger.info(f"Generating research-grounded script for '{topic}' via {model_name}...")
    response = llm._generate_response(prompt, app_config=app_cfg)
    if not response or response.startswith("Error:"):
        raise RuntimeError(f"Script generation failed: {response}")

    script = response.strip()
    # Clean any LLM meta-commentary, word counts, or markdown notes
    script = re.sub(r'\[\s*(?:word\s*count|words?|note|duration|hook).*?\]', '', script, flags=re.IGNORECASE)
    script = re.sub(r'\(\s*(?:word\s*count|words?|note|duration|hook).*?\)', '', script, flags=re.IGNORECASE)
    script = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', script)
    script = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', script, flags=re.IGNORECASE)
    script = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', script, flags=re.IGNORECASE)
    return script.strip()


def generate_visual_terms(script: str, topic: str, profile: dict) -> list[dict]:
    """Generate high-precision search queries for each visual beat in the narrative."""
    prompt = f"""Given this documentary video script about '{topic}', break it down into 4 to 6 sequential visual scene beats.
For each beat, specify a punchy 1 to 2 word search term for dynamic, cinematic vertical motion video B-roll (drone shots, moving landscape, atmospheric action, slow motion).

IMPORTANT VISUAL SEARCH RULES:
- Queries must be 1 to 2 words ONLY describing cinematic motion footage.
  * If polar / cold / Antarctica -> "glacier ice", "iceberg ocean", "snow blizzard", "drone mountains", "polar water".
  * If historical / ancient -> "ancient ruins", "desert storm", "torch fire", "dark forest", "drone castle".
  * If tech / future -> "datacenter server", "microchip", "cyber neon", "drone city night".
- NEVER use abstract phrases, sentence fragments, or generic words like "history" or "discovery".

Script:
"{script}"

Return a JSON array of 4 to 6 objects where each object has:
- "query": 1 to 2 word cinematic video search term (e.g. "glacier ice", "drone mountains")
- "fallback": 1 word fallback term (e.g. "iceberg", "snow")

Return ONLY the raw JSON array. No explanations, no markdown formatting."""

    app_cfg = _get_llm_config(profile)
    response = llm._generate_response(prompt, app_config=app_cfg)
    
    try:
        cleaned = response.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            cleaned = parts[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        items = json.loads(cleaned.strip())
        if isinstance(items, list) and items:
            return items
    except Exception as e:
        logger.debug(f"JSON visual beat parsing fallback: {e}")

    terms = [t.strip().strip('"').strip("'") for t in response.split(",") if t.strip()]
    return [{"query": t, "fallback": topic} for t in terms[:6]]


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
        # STAGE 1: Research, Topic & Grounded Script
        # -------------------------------------------------------------
        if state["stage"] in {"start", "script_generating"}:
            print(f"\n[1/5] Researching & writing script for '{state['topic']}'...")
            
            # Step 1A: Research factual evidence
            research_data = state.get("research")
            if not research_data:
                research_data = fetch_topic_research(state["topic"])
                state["research"] = research_data

            # Step 1B: Generate grounded script, viral title, and thumbnail hook
            script = state.get("script")
            if not script:
                script = generate_niche_script(profile, state["topic"], research_context=research_data.get("context", ""))
                visual_beats = generate_visual_terms(script, state["topic"], profile)
                
                # Generate viral title & thumbnail concept
                from core.thumbnail_generator import generate_title_and_thumbnail_concepts
                thumb_concept = generate_title_and_thumbnail_concepts(script, state["topic"], profile)

                state.update({
                    "script": script,
                    "visual_beats": visual_beats,
                    "title": thumb_concept.get("title", f"The Mystery of {state['topic']}"),
                    "thumbnail_text": thumb_concept.get("thumbnail_text", "THEY HID THIS"),
                    "thumbnail_prompt": thumb_concept.get("thumbnail_prompt", f"dramatic cinematic shot of {state['topic']}"),
                    "stage": "script_generated"
                })
                checkpoint.save(state)
            
            # Immediately unload Ollama LLM to free all 6.7 GB VRAM for GPU rendering
            try:
                import requests
                app_cfg = _get_llm_config(profile)
                m_name = app_cfg.get("ollama_model_name", "qwen3-coder-agent:latest")
                requests.post("http://127.0.0.1:11434/api/generate", json={"model": m_name, "keep_alive": 0}, timeout=5)
                logger.info("Unloaded Ollama LLM from GPU memory (RTX 2070 VRAM is 100% free).")
            except Exception as _e:
                logger.debug(f"Ollama unload skipped: {_e}")

            print(f"  ✓ Researched: '{research_data.get('title', state['topic'])}'")
            print(f"  ✓ Script ready ({len(script.split())} words)")
            query_preview = [b["query"] if isinstance(b, dict) else str(b) for b in state.get("visual_beats", [])]
            print(f"  ✓ Scene beats ({len(query_preview)}): {', '.join(query_preview)}")

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
            
            # Export word-level cues for mature bold karaoke synchronization
            word_cues = []
            if hasattr(sub_maker, "cues") and sub_maker.cues:
                for c in sub_maker.cues:
                    word_cues.append({
                        "word": str(c.content).strip(),
                        "start": float(c.start.total_seconds()),
                        "end": float(c.end.total_seconds())
                    })
            karaoke_json_file = os.path.join(task_dir, "karaoke.json")
            with open(karaoke_json_file, "w", encoding="utf-8") as kf:
                json.dump(word_cues, kf, indent=2)
            print(f"  ✓ Extracted {len(word_cues)} word-level cues for mature bold karaoke highlighting.")

            if not os.path.exists(subtitle_path):
                raise RuntimeError("Failed to generate subtitle track.")

            state.update({
                "subtitle_path": subtitle_path,
                "karaoke_path": karaoke_json_file,
                "stage": "subtitle_generated"
            })
            checkpoint.save(state)
            print(f"  ✓ Subtitles ready ({subtitle_path})")

        # -------------------------------------------------------------
        # STAGE 4: Authentic Visual Harvesting & NVENC Ken Burns Motion
        # -------------------------------------------------------------
        if state["stage"] in {"subtitle_generated", "materials_sourcing"}:
            print(f"\n[4/5] Harvesting authentic visual evidence & rendering Ken Burns camera motion...")
            visual_cfg = profile.get("visual", {})
            aspect = VideoAspect(visual_cfg.get("aspect_ratio", "9:16"))
            clip_dur = float(visual_cfg.get("clip_duration", 3.2))
            audio_duration = float(state["audio_duration"])
            needed_clips = max(1, math.ceil(audio_duration / clip_dur))
            
            fetcher = VisualFetcher()
            comfy = ComfyClient()
            cinematic_clips = []
            used_video_urls = set()
            
            beats = state.get("visual_beats", [])
            if not beats:
                beats = [{"query": state["topic"], "fallback": state["topic"]}]

            for i in range(needed_clips):
                beat = beats[i % len(beats)]
                query = beat.get("query") if isinstance(beat, dict) else str(beat)
                fallback = beat.get("fallback", state["topic"]) if isinstance(beat, dict) else state["topic"]
                
                img_path = os.path.join(task_dir, f"scene_evidence_{i+1}.jpg")
                clip_path = os.path.join(task_dir, f"scene_motion_{i+1}.mp4")
                
                acquired_video = None
                
                # Priority 1: Sourcing Real High-Definition Vertical Motion Video (Pexels)
                candidate_video_queries = [
                    query,
                    fallback,
                    f"{state['topic']} {query}",
                    f"{state['topic']} drone",
                    f"{state['topic']} cinematic",
                    f"{state['topic']} ocean" if "antarctica" in state['topic'].lower() else f"{state['topic']} landscape",
                    "cinematic drone flyover",
                    "dramatic weather timelapse",
                    "cinematic storm landscape"
                ]

                for q_try in candidate_video_queries:
                    if not q_try:
                        continue
                    clean_q = q_try.strip()
                    try:
                        pexels_items = material.search_videos_pexels(
                            search_term=clean_q,
                            minimum_duration=3,
                            video_aspect=aspect
                        )
                        for item in pexels_items:
                            if item.url not in used_video_urls:
                                dl_path = material.save_video(item.url, save_dir=task_dir)
                                if dl_path and os.path.exists(dl_path) and os.path.getsize(dl_path) > 10000:
                                    used_video_urls.add(item.url)
                                    acquired_video = dl_path
                                    print(f"  ✓ Scene {i+1}/{needed_clips}: Acquired Real Motion Video for '{clean_q}' ({item.duration}s)")
                                    break
                        if acquired_video:
                            break
                    except Exception as p_err:
                        logger.debug(f"Pexels motion video search failed for '{clean_q}': {p_err}")

                if acquired_video:
                    cinematic_clips.append(acquired_video)
                    continue

                # Priority 2: ComfyUI SDXL or Archival Image with NVENC Ken Burns Motion (Fallback ONLY)
                print(f"  ℹ Motion video unavailable for '{query}', falling back to high-res still with NVENC Ken Burns...")
                acquired_img = False
                if comfy.is_alive():
                    acquired_img = comfy.generate_scene_image(query, img_path)
                if not acquired_img:
                    acquired_img = fetcher.harvest_visual_for_scene(query, img_path, fallback_query=fallback)
                if not acquired_img:
                    acquired_img = fetcher.harvest_visual_for_scene(state["topic"], img_path)

                if acquired_img and os.path.exists(img_path):
                    image_to_cinematic_clip(
                        image_path=img_path,
                        output_clip_path=clip_path,
                        duration=clip_dur,
                        preset_index=i,
                        fps=30
                    )
                    cinematic_clips.append(clip_path)
                    print(f"  ✓ Scene {i+1}/{needed_clips}: Animated still '{query}' via NVENC Ken Burns")
                else:
                    logger.warning(f"Failed to acquire visual for scene {i+1}")

            if not cinematic_clips:
                raise RuntimeError("Failed to acquire authentic visual footage.")

            state.update({
                "materials": cinematic_clips,
                "stage": "materials_ready"
            })
            checkpoint.save(state)
            print(f"  ✓ Successfully produced {len(cinematic_clips)} animated visual scene clips.")

        # -------------------------------------------------------------
        # STAGE 5: Hardware-Accelerated NVENC Compositing
        # -------------------------------------------------------------
        if state["stage"] in {"materials_ready", "video_rendering"}:
            print(f"\n[5/5] Hardware-rendering final video via NVENC on RTX 2070...")
            combined_video_path = os.path.join(task_dir, "combined.mp4")
            final_video_path = os.path.join(task_dir, "final.mp4")
            
            visual_cfg = profile.get("visual", {})
            aspect = VideoAspect(visual_cfg.get("aspect_ratio", "9:16"))
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
                font_name="Montserrat-Black.ttf",
                font_size=subtitle_cfg.get("font_size", 52),
                text_fore_color=subtitle_cfg.get("color", "#FFFFFF"),
                text_background_color=subtitle_cfg.get("text_background_color", "#000000"),
                rounded_subtitle_background=subtitle_cfg.get("rounded_subtitle_background", True),
                stroke_color="#000000",
                stroke_width=subtitle_cfg.get("stroke_width", 2),
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

            # Step 6B: Render High-CTR Viral Thumbnail via ComfyUI SDXL & Bold Typography
            thumb_path = out_dir / "thumbnail.jpg"
            try:
                from core.thumbnail_generator import render_thumbnail_image
                print(f"  🎨 Generating High-CTR Thumbnail via ComfyUI SDXL...")
                render_thumbnail_image(
                    concept={
                        "thumbnail_text": state.get("thumbnail_text", "THEY HID THIS"),
                        "thumbnail_prompt": state.get("thumbnail_prompt", f"dramatic cinematic shot of {state['topic']}")
                    },
                    output_path=str(thumb_path),
                    font_path="resource/fonts/Montserrat-Black.ttf"
                )
            except Exception as t_err:
                logger.warning(f"Thumbnail generation error: {t_err}")

            # Build rich syndication metadata
            metadata = {
                "task_id": task_id,
                "niche": niche_slug,
                "title": state.get("title", f"The Secret of {state['topic']}"),
                "topic": state["topic"],
                "script": state["script"],
                "thumbnail_file": str(thumb_path.resolve()) if thumb_path.exists() else "",
                "duration_seconds": state["audio_duration"],
                "file_path": str(dest_mp4.resolve()),
                "file_size_mb": round(dest_mp4.stat().st_size / (1024 * 1024), 2),
                "visual_terms": state.get("visual_beats") or state.get("terms", []),
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
            print(f"  • Title:     {metadata['title']}")
            print(f"  • Subject:   {metadata['topic']}")
            print(f"  • Duration:  {metadata['duration_seconds']}s")
            print(f"  • Size:      {metadata['file_size_mb']} MB")
            print(f"  • Video:     {dest_mp4}")
            print(f"  • Thumbnail: {metadata['thumbnail_file']}")
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
