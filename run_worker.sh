#!/usr/bin/env bash
# ==============================================================================
# Autonomous Video Worker CLI Controller
# ==============================================================================

set -e

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

if command -v ffmpeg >/dev/null 2>&1; then
    export IMAGEIO_FFMPEG_EXE="$(command -v ffmpeg)"
elif [ -f "/usr/bin/ffmpeg" ]; then
    export IMAGEIO_FFMPEG_EXE="/usr/bin/ffmpeg"
fi

if [ -f "${DIR}/.venv/bin/python" ]; then
    VENV_PYTHON="${DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    VENV_PYTHON="$(command -v python3)"
else
    VENV_PYTHON="python"
fi

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

show_banner() {
    echo -e "${CYAN}======================================================${NC}"
    echo -e "${CYAN}${BOLD}   🎬 Starting MoneyPrinter Studio Automation Stack   ${NC}"
    echo -e "${CYAN}======================================================${NC}"
    echo -e "${BLUE}[Media Core]${NC} Initializing Cinema Engine & Pure FFmpeg NVENC..."
    echo -e "${BLUE}[Director]${NC}   AI Movie Director & Shot Planner ready."
    echo -e "${BLUE}[Asset Pool]${NC} Pexels HD, Pixabay, & ComfyUI SDXL active."
    echo -e "${BLUE}[Pipeline]${NC}   Press ${YELLOW}Ctrl+C${NC} at any time to pause or save checkpoint."
    echo -e "${CYAN}------------------------------------------------------${NC}"
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
    series)
        show_banner
        shift
        PROFILE="${1:-true_crime}"
        COUNT="${2:-5}"
        echo -e "${GREEN}[Series] Launching ${COUNT}-episode series for niche: ${PROFILE}${NC}"
        "$VENV_PYTHON" run_series.py --profile "$PROFILE" --count "$COUNT"
        ;;
    infinite)
        show_banner
        echo -e "${GREEN}[Autonomous Daemon] Launching Infinite Multi-Niche Engine across all niches...${NC}"
        "$VENV_PYTHON" run_infinite_generator.py
        ;;
    *)
        echo -e "${GREEN}Autonomous Video Worker CLI${NC}"
        echo ""
        echo "Usage: ./run_worker.sh <command>"
        echo ""
        echo "Commands:"
        echo "  start [profile]        Start single pipeline run for profile (default: dark_history)"
        echo "  series [profile] [N]   Launch multi-episode series (default: true_crime, 5 episodes)
  infinite               Run continuous autonomous generator across all niches overnight"
        echo "  status                 View active checkpoints and generated videos"
        echo "  profiles               List available niche profiles"
        echo "  clear [profile]        Manually clear a profile checkpoint"
        echo ""
        exit 1
        ;;
esac
