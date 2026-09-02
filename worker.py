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
import re
from pathlib import Path
from uuid import uuid4

from loguru import logger

# Route low-level library debug spam to pipeline_debug.log, keeping stdout clean & informative
try:
    logger.remove()
    logger.add("pipeline_debug.log", rotation="25 MB", retention="5 days", level="DEBUG", encoding="utf-8")
    logger.add(sys.stderr, level="ERROR", format="<red>[ERROR]</red> {message}")
except Exception:
    pass

from app.config import config
from app.models.schema import VideoAspect, VideoConcatMode, VideoParams
from app.services import llm, material, subtitle, video, voice
from app.utils import utils
from core.checkpoint import CheckpointManager
def get_sentence_scenes(srt_path: str, audio_duration: float, min_dur: float = 2.5, max_dur: float = 4.8) -> list:
    """Parses subtitle.srt into frame-accurate, contiguous narrative scenes synchronized to narrator speech pauses."""
    if not os.path.exists(srt_path):
        return []
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = r"(\d+)\n(\d{2}:\d{2}:\d{2}[,\.]\d{3}) --> (\d{2}:\d{2}:\d{2}[,\.]\d{3})\n(.*?)(?=\n\n|\n*$)"
    matches = re.findall(pattern, content, re.DOTALL)

    def to_sec(ts):
        ts = ts.replace(",", ".")
        h, m, s = ts.split(":")
        return int(h)*3600 + int(m)*60 + float(s)

    items = []
    for m in matches:
        s_sec = to_sec(m[1])
        e_sec = to_sec(m[2])
        text = m[3].replace("\n", " ").strip()
        items.append({"start": s_sec, "end": e_sec, "text": text})

    if not items:
        return []

    scenes = []
    curr = None
    for item in items:
        if curr is None:
            curr = {"start": item["start"], "end": item["end"], "text": item["text"]}
        else:
            cand_dur = item["end"] - curr["start"]
            if cand_dur <= max_dur and (curr["end"] - curr["start"] < min_dur or not curr["text"].endswith((".", "!", "?"))):
                curr["end"] = item["end"]
                curr["text"] += " " + item["text"]
            else:
                scenes.append(curr)
                curr = {"start": item["start"], "end": item["end"], "text": item["text"]}
    if curr:
        scenes.append(curr)

    # Adjust contiguous boundaries to cover 0.0 -> audio_duration with zero micro-gaps
    for i in range(len(scenes)):
        if i == 0:
            scenes[i]["start"] = 0.0
        else:
            scenes[i]["start"] = scenes[i-1]["end"]

        if i == len(scenes) - 1:
            scenes[i]["end"] = max(scenes[i]["start"] + 1.0, float(audio_duration))
        else:
            next_start = items[min(len(items)-1, i+1)]["start"]
            if next_start > scenes[i]["end"]:
                scenes[i]["end"] = next_start
        scenes[i]["duration"] = round(scenes[i]["end"] - scenes[i]["start"], 3)

    return scenes


def _slugify(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9\s_-]", "", text).strip()
    return re.sub(r"[\s_-]+", "_", clean).lower()

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

    target_cfg = profile.get("video_target", {})
    min_w = target_cfg.get("min_words", 160)
    max_w = target_cfg.get("max_words", 185)

    context_block = f"\n## Verified Archival Evidence & Intel:\n{research_context}\n" if research_context else ""

    return f"""# Role: Master Investigative Documentarian & Viral Short-Form Screenwriter
# Niche: {profile['niche']['name']} ({profile['niche']['description']})
# Subject: {topic}
{context_block}

## 4-Act High-Retention Viral Architecture:
1. ACT I — THE ANOMALY HOOK (0-8s):
   - Open with a jarring contradiction, impossible physical evidence, or cognitive dissonance.
   - Never introduce yourself or say hello. Jump straight into the heart of the mystery.

2. ACT II — THE INVESTIGATION & EVIDENCE (8-35s):
   - Build narrative momentum with concrete forensic facts: exact dates, classified dossier codenames, physical measurements, or eyewitness testimony.
   - Use staccato spoken cadence: keep sentences short (6 to 12 words max) with natural breathing pauses.

3. ACT III — THE MIDPOINT ESCALATION (35-52s):
   - Just when the viewer thinks they understand the story, introduce the fatal paradox: conflicting laboratory data, suppressed archives, or an impossible anomaly that defies explanation.

4. ACT IV — THE UNSETTLING REVELATION & INFINITE LOOP (52-70s):
   - Deliver a chilling concluding insight that leaves the viewer questioning what they know.
   - Craft the final sentence so it flows seamlessly into the very first sentence, creating an infinite retention loop on TikTok and YouTube Shorts.

## Absolute Constraints:
- Target Spoken Length: Strictly between {min_w} and {max_w} words (calibrated for exactly 65 to 75 seconds of narration).
- Forbidden Clichés: Absolutely NEVER use {banned_str} or phrases like "in this video", "have you ever wondered", "dive into", "let me explain".
- Spoken Cadence: Write strictly for audio narration. No markdown asterisks, no headers, no quotation marks, no narrator tags, and zero bracketed notes.
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
    script = re.sub(r'\[.*?\]|\(.*?\)', '', script)
    script = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', script)
    script = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', script, flags=re.IGNORECASE)
    script = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', script, flags=re.IGNORECASE)

    target_cfg = profile.get("video_target", {})
    min_w = target_cfg.get("min_words", 160)
    max_w = target_cfg.get("max_words", 185)
    word_count = len(script.split())

    # Mandatory expansion loop: enforce strictly 65-75+ second duration
    if word_count < min_w:
        logger.info(f"Draft script is only {word_count} words. Auto-expanding to target {min_w}-{max_w} words for 65s+ duration...")
        expand_prompt = f"""You are the Master Documentarian. The draft script below is only {word_count} words, which is too short for a 65-75 second documentary.
Expand this script to strictly between {min_w} and {max_w} words by enriching it with concrete historical evidence, specific dates, forensic details, and intense narrative tension.

Draft Script:
\"{script}\"

Return ONLY the final expanded script to be read aloud. No labels, no headers, no word counts."""
        expanded_resp = llm._generate_response(expand_prompt, app_config=app_cfg)
        if expanded_resp and not expanded_resp.startswith("Error:"):
            exp_clean = re.sub(r'\[.*?\]|\(.*?\)', '', expanded_resp)
            exp_clean = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', exp_clean)
            exp_clean = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', exp_clean, flags=re.IGNORECASE)
            exp_clean = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', exp_clean, flags=re.IGNORECASE)
            script = exp_clean.strip()

    return script.strip()


def generate_visual_terms(script: str, topic: str, profile: dict) -> list[dict]:
    """Generate high-precision search queries and hybrid generation routes anchored to the topic."""
    prompt = f"""Given this documentary video script about '{topic}', break it down into 6 to 8 sequential visual scene beats.
For each beat, categorize it as:
- "stock": for filmable real-world footage (aerial landscapes, drone shots, buildings, factories, nature, city streets, machinery).
- "generate": for unfilmable scenes that do NOT exist in stock footage (deep pitch-black subterranean ice caverns, microscopic extremophiles/bacteria, planetary surfaces of Europa/Mars, inside nanometer laser vacuum chambers, ancient lost tombs).

CRITICAL SEARCH & RELEVANCE RULES:
- EVERY query MUST be strictly relevant to '{topic}' and the actual beat described in the script.
- For "stock" beats, specify a cinematic motion search query and 1 to 2 "negative_terms" to explicitly avoid irrelevant misfires (e.g. for polar ice: negative_terms: ["fishing", "scuba", "beach"]; for semiconductor tech: negative_terms: ["food", "casino", "nature"]).
- For "generate" beats, write a descriptive photorealistic SDXL prompt set in '{topic}' (8k, volumetric lighting, national geographic, textless).

Script:
"{script}"

Return a JSON array of 6 to 8 objects where each object has:
- "query": descriptive cinematic query or SDXL prompt for '{topic}'
- "fallback": 1-2 word fallback term
- "type": "stock" or "generate"
- "negative_terms": list of 1 to 3 words to avoid (e.g. ["fishing", "beach"])

Return ONLY the raw JSON array. No explanations, no markdown formatting."""

    app_cfg = _get_llm_config(profile)
    response = llm._generate_response(prompt, app_config=app_cfg)
    
    items = []
    try:
        cleaned = response.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            cleaned = parts[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        parsed = json.loads(cleaned.strip())
        if isinstance(parsed, list):
            items = parsed
    except Exception as e:
        logger.debug(f"JSON visual beat parsing fallback: {e}")

    if not items:
        terms = [t.strip().strip('"').strip("'") for t in response.split(",") if t.strip()]
        items = [{"query": t, "fallback": topic, "type": "stock", "negative_terms": []} for t in terms[:8]]

    # Post-process: Guarantee every query is anchored to the topic domain
    anchored_beats = []
    topic_clean = topic.strip()
    topic_words = set(topic_clean.lower().split())

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_q = re.sub(r'["\']', '', item.get("query", "")).strip()
        raw_fb = re.sub(r'["\']', '', item.get("fallback", topic_clean)).strip()
        beat_type = item.get("type", "stock").lower()
        if beat_type not in {"stock", "generate"}:
            beat_type = "stock"

        neg_terms = item.get("negative_terms", [])
        if not isinstance(neg_terms, list):
            neg_terms = []

        if not raw_q:
            continue

        q_words = set(raw_q.lower().split())
        # Anchor with topic if not already present
        if not (topic_words & q_words):
            anchored_q = f"{topic_clean} {raw_q}".strip()
        else:
            anchored_q = raw_q

        fb_words = set(raw_fb.lower().split())
        if not (topic_words & fb_words):
            anchored_fb = f"{topic_clean} {raw_fb}".strip()
        else:
            anchored_fb = raw_fb

        anchored_beats.append({
            "query": anchored_q,
            "fallback": anchored_fb,
            "type": beat_type,
            "negative_terms": [str(t).lower().strip() for t in neg_terms if str(t).strip()]
        })

    return anchored_beats if anchored_beats else [{"query": topic_clean, "fallback": topic_clean, "type": "stock", "negative_terms": []}]


def run_worker_pipeline(profile_path: str, topic_override: str = None, clear_state: bool = False, episode_num: int = None):
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
            "episode_num": episode_num,
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
            # Free ComfyUI models from VRAM before invoking Ollama LLM
            try:
                import requests as _req
                _req.post("http://127.0.0.1:8188/free", json={"unload_models": True, "free_memory": True}, timeout=3)
            except Exception:
                pass

            script = state.get("script")
            if not script:
                script = generate_niche_script(profile, state["topic"], research_context=research_data.get("context", ""))
                
                # Generate viral title & thumbnail concept
                from core.thumbnail_generator import generate_title_and_thumbnail_concepts
                thumb_concept = generate_title_and_thumbnail_concepts(script, state["topic"], profile)

                state.update({
                    "script": script,
                    "title": thumb_concept.get("title", f"The Mystery of {state['topic']}"),
                    "thumbnail_text": thumb_concept.get("thumbnail_text", "THEY HID THIS"),
                    "thumbnail_prompt": thumb_concept.get("thumbnail_prompt", f"dramatic cinematic shot of {state['topic']}"),
                    "stage": "script_generated"
                })
                checkpoint.save(state)

            print(f"  ✓ Researched: '{research_data.get('title', state['topic'])}'")
            print(f"  ✓ Script ready ({len(state['script'].split())} words)")

        # -------------------------------------------------------------
        # STAGE 2: Voice Narration (Edge TTS or configured provider)
        # -------------------------------------------------------------
        if state["stage"] in {"script_generated", "audio_generating"}:
            print(f"\n[2/5] Synthesizing voiceover narration...")
            audio_file = os.path.join(task_dir, "audio.mp3")
            voice_config = profile.get("voice", {})
            voice_name = voice_config.get("voice_name", "en-US-ChristopherNeural")
            voice_rate = float(voice_config.get("voice_rate", 1.0))

            # Ensure 100% clean script before voice synthesis (zero bracketed metrics or word counts)
            clean_script = re.sub(r'\[.*?\]|\(.*?\)', '', state["script"]).strip()
            clean_script = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', clean_script).strip()
            clean_script = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', clean_script, flags=re.IGNORECASE).strip()
            clean_script = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', clean_script, flags=re.IGNORECASE).strip()
            state["script"] = clean_script

            sub_maker = voice.tts(
                text=clean_script,
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
        # STAGE 4: AI Movie Director — Storyboard & Visual Harvesting
        # -------------------------------------------------------------
        if state["stage"] in {"subtitle_generated", "materials_sourcing"}:
            print(f"\n[4/5] AI Movie Director directing storyboard & harvesting visuals (ZERO ASSET REUSE)...")
            from core.cinema_engine import get_sentence_scenes, harvest_unique_timeline
            from core.director import MovieDirector, DirectorShot
            import dataclasses

            audio_duration = float(state["audio_duration"])
            sentence_scenes = get_sentence_scenes(state["subtitle_path"], audio_duration)
            needed_clips = len(sentence_scenes) if sentence_scenes else max(18, math.ceil(audio_duration / 2.8))
            print(f"  🎬 Cinema Director: Planning {needed_clips} unique scenes with frame-accurate speech alignment.")

            director = MovieDirector(profile)
            cached_shots = state.get("director_shots")
            if cached_shots and isinstance(cached_shots, list):
                shots = [DirectorShot(**s) for s in cached_shots]
            else:
                shots = director.plan_shotlist(state["script"], state["topic"], needed_clips)
                state["director_shots"] = [dataclasses.asdict(s) for s in shots]
                checkpoint.save(state)

                # Free Ollama memory
                try:
                    import requests as _req
                    app_cfg = _get_llm_config(profile)
                    m_name = app_cfg.get("ollama_model_name", "qwen3-coder-agent:latest")
                    _req.post("http://127.0.0.1:11434/api/generate", json={"model": m_name, "keep_alive": 0}, timeout=5)
                except Exception:
                    pass

            cinematic_clips = harvest_unique_timeline(
                scenes=sentence_scenes,
                task_dir=task_dir,
                topic=state["topic"],
                profile=profile,
                director_shots=shots
            )

            if not cinematic_clips:
                raise RuntimeError("Failed to acquire authentic visual footage.")

            state.update({
                "materials": cinematic_clips,
                "stage": "materials_ready"
            })
            checkpoint.save(state)
            print(f"  ✓ Successfully produced {len(cinematic_clips)} 100% UNIQUE visual scene clips (ZERO repetition).")

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
            
            # Step A: Hardware concatenate all unique clips via pure FFmpeg NVENC (Zero repetition)
            from core.cinema_engine import assemble_final_nvenc_video
            assemble_final_nvenc_video(
                clip_files=state["materials"],
                audio_file=state["audio_file"],
                output_file=combined_video_path
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
            ep_num = state.get("episode_num")
            clean_slug = _slugify(state["topic"])[:45]
            if ep_num is not None:
                folder_name = f"{int(ep_num):02d}_{clean_slug}"
            else:
                folder_name = f"{time.strftime('%Y%m%d_%H%M')}_{clean_slug}"

            out_dir = Path("output") / niche_slug / folder_name
            out_dir.mkdir(parents=True, exist_ok=True)

            dest_mp4 = out_dir / f"{folder_name}.mp4"
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
            checkpoint.archive(str(Path("storage") / "checkpoints" / niche_slug), task_id)

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
    parser.add_argument("--episode", type=int, default=None, help="Episode number in series (e.g. 1, 2, 3)")
    args = parser.parse_args()

    run_worker_pipeline(
        profile_path=args.profile,
        topic_override=args.topic,
        clear_state=args.clear_state,
        episode_num=args.episode
    )
