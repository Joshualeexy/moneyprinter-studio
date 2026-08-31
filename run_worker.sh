#!/usr/bin/env bash
# ==============================================================================
# Autonomous Video Worker CLI Controller
# ==============================================================================

set -e

DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

export IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg
VENV_PYTHON="${DIR}/.venv/bin/python"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

show_banner() {
    echo -e "${BLUE}======================================================${NC}"
    echo -e "${BLUE}       🎬 Autonomous Niche Video Engine Worker         ${NC}"
    echo -e "${BLUE}======================================================${NC}"
}

case "$1" in
    start)
        show_banner
        shift
        PROFILE="${1:-dark_history}"
        if [ "$#" -gt 0 ]; then shift; fi
        echo -e "${GREEN}[Worker] Launching niche profile: ${PROFILE}${NC}"
        "$VENV_PYTHON" worker.py --profile "$PROFILE" "$@"
        ;;
    status)
        show_banner
        echo -e "${BLUE}=== Active Worker Checkpoints ===${NC}"
        CHECKPOINTS=$(ls -1 pipeline_state_*.json 2>/dev/null || true)
        if [ -n "$CHECKPOINTS" ]; then
            for cp in $CHECKPOINTS; do
                NICHE=$(echo "$cp" | sed 's/pipeline_state_//;s/\.json//')
                STAGE=$(grep -o '"stage": *"[^"]*"' "$cp" 2>/dev/null | head -1 || echo "unknown")
                STATUS=$(grep -o '"status": *"[^"]*"' "$cp" 2>/dev/null | head -1 || echo "unknown")
                TOPIC=$(grep -o '"topic": *"[^"]*"' "$cp" 2>/dev/null | head -1 || echo "none")
                echo -e "  • ${YELLOW}${NICHE}${NC}: $STATUS | $STAGE | $TOPIC"
            done
        else
            echo "  No active checkpoints. All workers idle."
        fi
        echo ""
        echo -e "${BLUE}=== Output Archives ===${NC}"
        if [ -d "output" ]; then
            find output -name "*.mp4" | while read -r f; do
                SIZE=$(du -h "$f" | cut -f1)
                echo -e "  ✓ ${GREEN}$f${NC} ($SIZE)"
            done
        else
            echo "  No rendered outputs yet."
        fi
        ;;
    profiles)
        echo -e "${BLUE}=== Available Niche Profiles ===${NC}"
        ls -1 profiles/*.toml 2>/dev/null | sed 's/profiles\///;s/\.toml//' | while read -r p; do
            echo -e "  • ${GREEN}$p${NC}"
        done
        ;;
    clear)
        PROFILE="${2:-dark_history}"
        echo -e "${YELLOW}Clearing checkpoint for profile: $PROFILE${NC}"
        rm -f "pipeline_state_${PROFILE}.json"
        echo -e "${GREEN}Done.${NC}"
        ;;
    *)
        echo -e "${GREEN}Autonomous Video Worker CLI${NC}"
        echo ""
        echo "Usage: ./run_worker.sh <command>"
        echo ""
        echo "Commands:"
        echo "  start [profile]        Start pipeline for profile (default: dark_history)"
        echo "  start [profile] --clear-state    Clear state and start fresh video"
        echo "  status                 View active checkpoints and generated videos"
        echo "  profiles               List available niche profiles"
        echo "  clear [profile]        Manually clear a profile checkpoint"
        echo ""
        exit 1
        ;;
esac
