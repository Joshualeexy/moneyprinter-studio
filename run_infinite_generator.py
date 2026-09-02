#!/usr/bin/env python3
"""
==============================================================================
Infinite Multi-Niche Autonomous Video Engine (run_infinite_generator.py)
==============================================================================
Runs continuously across all niches, producing full 5-episode narrative series:
  • Frame-accurate sentence-timed visual cuts (zero audio desync)
  • Clean chronological folder naming (01_..., 02_..., 03_...)
  • ComfyUI SDXL Hero Scene cinematics
  • Pexels 1080x1920 HD vertical footage
  • Montserrat-Black fitted dark pill karaoke subtitles
  • ComfyUI High-CTR Thumbnails
  • Automated VRAM memory handshakes (zero CUDA OOM)
  • Self-healing crash recovery & smart resumption
==============================================================================
"""

import os
import sys
import time
import requests
from pathlib import Path
from loguru import logger

# Route detailed debug to log file, keeping stdout clean
try:
    logger.remove()
    logger.add("pipeline_debug.log", rotation="25 MB", retention="5 days", level="DEBUG", encoding="utf-8")
    logger.add(sys.stderr, level="ERROR", format="<red>[ERROR]</red> {message}")
except Exception:
    pass

from run_series import run_series
from core.profile_loader import load_profile

def get_niches_queue():
    """Dynamically scans all profiles in profiles/*.toml to queue every available niche."""
    profiles_dir = Path("profiles")
    discovered = []
    priority_order = [
        "prehistoric",
        "unsolved_mysteries",
        "military_black_ops",
        "forbidden_archaeology",
        "space_anomalies",
        "deep_sea",
        "psychology",
        "true_crime",
        "money_heists",
        "mythology",
        "tech",
        "ancient",
        "dark_history",
    ]
    seen = set()
    for slug in priority_order:
        p_path = profiles_dir / f"{slug}.toml"
        if p_path.exists():
            discovered.append((slug, 5))
            seen.add(slug)
    for p in sorted(profiles_dir.glob("*.toml")):
        if p.stem not in seen:
            discovered.append((p.stem, 5))
    return discovered


def free_all_gpu_vram():
    """Flushes both ComfyUI and Ollama weights from GPU memory."""
    try:
        requests.post("http://127.0.0.1:8188/free", json={"unload_models": True, "free_memory": True}, timeout=3)
    except Exception:
        pass
    try:
        for model in ["qwen3-coder-agent:latest", "qwen3:8b", "qwen3-8b-agent:latest"]:
            requests.post("http://127.0.0.1:11434/api/generate", json={"model": model, "keep_alive": 0}, timeout=3)
    except Exception:
        pass


def log_generator(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    with open("infinite_pipeline.log", "a", encoding="utf-8") as f:
        f.write(formatted + "\n")


def run_infinite_loop():
    log_generator("=" * 65)
    log_generator("🚀 STARTING INFINITE MULTI-NICHE AUTONOMOUS GENERATOR")
    log_generator("=" * 65)

    cycle = 1
    while True:
        log_generator(f"\n🔄 --- STARTING PIPELINE CYCLE #{cycle} ---")
        
        for niche_slug, target_episodes in get_niches_queue():
            log_generator(f"\n========================================================")
            log_generator(f"🎬 NICHE QUEUE: [{niche_slug.upper()}] (Target: {target_episodes} Episodes)")
            log_generator(f"========================================================")
            
            # Flush VRAM before starting niche
            free_all_gpu_vram()
            time.sleep(2)

            try:
                run_series(profile_name=niche_slug, count=target_episodes)
                log_generator(f"✓ Niche [{niche_slug.upper()}] batch run finished successfully.")
            except Exception as e:
                log_generator(f"❌ Error during niche [{niche_slug.upper()}]: {e}")
                log_generator("Resuming next niche in 15 seconds...")
                time.sleep(15)

            # Flush VRAM after completing niche
            free_all_gpu_vram()
            time.sleep(3)

        log_generator(f"\n🎉 Cycle #{cycle} complete across all niches!")
        cycle += 1
        time.sleep(10)


if __name__ == "__main__":
    run_infinite_loop()
