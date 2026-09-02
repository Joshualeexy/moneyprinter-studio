#!/usr/bin/env python3
"""
==============================================================================
Autonomous Niche Video Series Runner (run_series.py)
==============================================================================
Generates a complete multi-episode video series sequentially across any niche.
Handles research, 30.5B scriptwriting, 8B movie directing, ComfyUI SDXL scenes,
and hardware NVENC video rendering for every episode in the arc.
==============================================================================
"""

import argparse
import json
import sys
import time
from pathlib import Path

from core.profile_loader import load_profile
from worker import run_worker_pipeline


def run_series(profile_name: str, count: int = 5):
    profile = load_profile(profile_name)
    niche_slug = profile["niche"]["slug"]
    series_cfg = profile.get("series", {})
    arc_title = series_cfg.get("current_arc", f"{profile['niche']['name']} Series")
    
    # Get pre-configured episodes or defaults
    episodes = series_cfg.get("episodes", [])
    if len(episodes) < count:
        default_episodes = [
            f"Mystery {i+1} of {arc_title}" for i in range(count)
        ]
        episodes = (episodes + default_episodes)[:count]
    else:
        episodes = episodes[:count]

    print("\n" + "=" * 65)
    print(f" 🎬 LAUNCHING {count}-EPISODE SERIES: [{niche_slug.upper()}]")
    print(f" Arc: {arc_title}")
    print("=" * 65)
    for i, ep in enumerate(episodes, 1):
        print(f"  {i}. {ep}")
    print("=" * 65 + "\n")

    completed_episodes = []

    # Scan existing output archive to detect already finished episodes
    niche_out_dir = Path("output") / niche_slug
    existing_topics = {}
    if niche_out_dir.exists():
        for d in niche_out_dir.iterdir():
            if d.is_dir():
                meta_file = d / "metadata.json"
                if meta_file.exists():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as mf:
                            m_data = json.load(mf)
                            t_name = m_data.get("topic", "").strip()
                            v_path = m_data.get("file_path", "")
                            if t_name and Path(v_path).exists() and Path(v_path).stat().st_size > 1024 * 1024:
                                existing_topics[t_name.lower()] = m_data
                    except Exception:
                        pass

    for idx, episode_topic in enumerate(episodes, 1):
        print("\n" + "#" * 65)
        print(f"  EPISODE {idx}/{count}: {episode_topic}")
        print("#" * 65)

        # Smart Resumption: Skip already rendered episodes
        if episode_topic.lower() in existing_topics:
            prev_meta = existing_topics[episode_topic.lower()]
            print(f"  ⚡ Episode {idx} already rendered and archived: '{prev_meta.get('title', episode_topic)}'")
            print(f"     Video: {prev_meta.get('file_path')}")
            print(f"     Skipping directly to next episode in arc!")
            completed_episodes.append({
                "episode": idx,
                "topic": episode_topic,
                "title": prev_meta.get("title", episode_topic),
                "duration": prev_meta.get("duration_seconds", 0),
                "size_mb": prev_meta.get("file_size_mb", 0),
                "video_path": prev_meta.get("file_path", ""),
                "thumbnail_path": prev_meta.get("thumbnail_file", ""),
                "elapsed_seconds": 0
            })
            continue

        start_time = time.time()
        try:
            run_worker_pipeline(
                profile_path=profile_name,
                topic_override=episode_topic,
                clear_state=True,
                episode_num=idx
            )

            # Find the most recently created folder in output/{niche_slug}
            niche_out_dir = Path("output") / niche_slug
            if niche_out_dir.exists():
                task_dirs = sorted([d for d in niche_out_dir.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime, reverse=True)
                if task_dirs:
                    latest_dir = task_dirs[0]
                    meta_file = latest_dir / "metadata.json"
                    if meta_file.exists():
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            completed_episodes.append({
                                "episode": idx,
                                "topic": episode_topic,
                                "title": meta.get("title", episode_topic),
                                "duration": meta.get("duration_seconds", 0),
                                "size_mb": meta.get("file_size_mb", 0),
                                "video_path": meta.get("file_path", ""),
                                "thumbnail_path": meta.get("thumbnail_file", ""),
                                "elapsed_seconds": int(time.time() - start_time)
                            })
        except Exception as e:
            print(f"\n❌ Episode {idx} failed: {e}")
            continue

    # Final Series Summary Card
    print("\n" + "=" * 65)
    print(f" 🎉 SERIES COMPLETE: {len(completed_episodes)}/{count} EPISODES READY")
    print(f" Niche: {profile['niche']['name']} | Arc: {arc_title}")
    print("=" * 65)
    for ep in completed_episodes:
        print(f"\n[Episode {ep['episode']}] {ep['title']}")
        print(f"  • Subject:   {ep['topic']}")
        print(f"  • Duration:  {ep['duration']}s | Size: {ep['size_mb']} MB")
        print(f"  • Video:     {ep['video_path']}")
        print(f"  • Thumbnail: {ep['thumbnail_path']}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Niche Series Runner")
    parser.add_argument("--profile", default="true_crime", help="Niche profile slug or path")
    parser.add_argument("--count", type=int, default=5, help="Number of episodes to generate")
    args = parser.parse_args()

    run_series(args.profile, count=args.count)
