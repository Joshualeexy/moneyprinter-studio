#!/usr/bin/env python3
"""
MoneyPrinter Studio - Autonomous AI Cinema Engine
Main CLI entry point forwarding to the worker orchestrator.
"""
import argparse
import sys
from worker import run_worker_pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MoneyPrinter Studio - Autonomous AI Cinema Engine")
    parser.add_argument("--profile", default="dark_history", help="Niche profile slug or path")
    parser.add_argument("--topic", default=None, help="Explicit topic override")
    parser.add_argument("--clear-state", action="store_true", help="Clear saved state and start fresh")
    parser.add_argument("--episode", type=int, default=None, help="Episode number in series (e.g. 1, 2, 3)")
    parser.add_argument("--gallery", "--server", action="store_true", help="Start the video gallery web server")
    args, unknown = parser.parse_known_args()

    if args.gallery or (len(sys.argv) > 1 and sys.argv[1] in ("gallery", "server")):
        from gallery import main as gallery_main
        gallery_main()
        sys.exit(0)

    run_worker_pipeline(
        profile_path=args.profile,
        topic_override=args.topic,
        clear_state=args.clear_state,
        episode_num=args.episode
    )

