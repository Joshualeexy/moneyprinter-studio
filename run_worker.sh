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

SCRAPER_DIR="${DIR}/services/scraper"
SCRAPER_PORT=4050
SCRAPER_PID=""
PIPELINE_PID=""

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

# ── Cleanup Handler on Exit / Ctrl+C ─────────────────────────────────────────
cleanup() {
    echo -e "\n${YELLOW}[Worker] Shutting down services...${NC}"
    
    # Terminate Python pipeline if running
    if [ -n "$PIPELINE_PID" ] && kill -0 "$PIPELINE_PID" 2>/dev/null; then
        echo -e "${YELLOW}[Pipeline] Stopping Python automation pipeline (PID: $PIPELINE_PID)...${NC}"
        kill -TERM "$PIPELINE_PID" 2>/dev/null || true
        wait "$PIPELINE_PID" 2>/dev/null || true
    fi

    # Terminate Node Scraper Service if we started it
    if [ -n "$SCRAPER_PID" ] && kill -0 "$SCRAPER_PID" 2>/dev/null; then
        echo -e "${YELLOW}[Scraper API] Stopping Node.js stealth scraper microservice (PID: $SCRAPER_PID)...${NC}"
        kill -TERM "$SCRAPER_PID" 2>/dev/null || true
    fi

    # Release port 4050 if bound
    fuser -k -n tcp "$SCRAPER_PORT" 2>/dev/null || true

    echo -e "${GREEN}[Stack] All automation services stopped cleanly. Goodbye!${NC}"
    exit 0
}

trap cleanup INT TERM

show_banner() {
    echo -e "${CYAN}======================================================${NC}"
    echo -e "${CYAN}${BOLD}   🎬 Starting MoneyPrinter Studio Automation Stack   ${NC}"
    echo -e "${CYAN}======================================================${NC}"
}

ensure_scraper() {
    if [ -d "$SCRAPER_DIR" ] && command -v node >/dev/null 2>&1; then
        if curl -s -m 2 "http://127.0.0.1:${SCRAPER_PORT}/health" 2>/dev/null | grep -q '"status":"ok"'; then
            echo -e "${GREEN}[Scraper API] Stealth search microservice active on port ${SCRAPER_PORT}${NC}"
        else
            echo -e "${BLUE}[Scraper API] Starting Node.js stealth scraper microservice...${NC}"
            cd "$SCRAPER_DIR"
            node server.js >/tmp/moneyprinter_scraper.log 2>&1 &
            SCRAPER_PID=$!
            cd "$DIR"

            echo -n "[Scraper API] Waiting for stealth browser pool to initialize..."
            WAITED=0
            READY=false
            while [ $WAITED -lt 25 ]; do
                if curl -s "http://127.0.0.1:${SCRAPER_PORT}/health" 2>/dev/null | grep -q '"browserReady":true'; then
                    READY=true
                    break
                fi
                echo -n "."
                sleep 1
                WAITED=$((WAITED + 1))
            done

            if [ "$READY" = true ]; then
                echo -e " ${GREEN}READY!${NC}"
            else
                echo -e " ${YELLOW}TIMEOUT (Hybrid will fallback to Wikipedia)${NC}"
            fi
        fi
    fi
    echo -e "${BLUE}[Pipeline] Starting Python automation pipeline...${NC}"
    echo -e "${BLUE}[Pipeline] Press ${YELLOW}Ctrl+C${NC}${BLUE} at any time to stop both pipeline and scraper.${NC}"
    echo -e "${CYAN}------------------------------------------------------${NC}"
}

case "$1" in
    start)
        show_banner
        shift
        PROFILE="${1:-dark_history}"
        if [ "$#" -gt 0 ]; then shift; fi
        ensure_scraper
        echo -e "${GREEN}[Worker] Launching niche profile: ${PROFILE}${NC}"
        "$VENV_PYTHON" worker.py --profile "$PROFILE" "$@" &
        PIPELINE_PID=$!
        wait "$PIPELINE_PID"
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
        echo -e "${BLUE}=== Scraper Microservice Status ===${NC}"
        if curl -s -m 2 "http://127.0.0.1:${SCRAPER_PORT}/health" 2>/dev/null | grep -q '"status":"ok"'; then
            echo -e "  • Status: ${GREEN}ONLINE (Port ${SCRAPER_PORT})${NC}"
        else
            echo -e "  • Status: ${YELLOW}OFFLINE (Will auto-launch on demand)${NC}"
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
        ensure_scraper
        echo -e "${GREEN}[Series] Launching ${COUNT}-episode series for niche: ${PROFILE}${NC}"
        "$VENV_PYTHON" run_series.py --profile "$PROFILE" --count "$COUNT" &
        PIPELINE_PID=$!
        wait "$PIPELINE_PID"
        ;;
    infinite)
        show_banner
        ensure_scraper
        echo -e "${GREEN}[Autonomous Daemon] Launching Infinite Multi-Niche Engine across all niches...${NC}"
        "$VENV_PYTHON" run_infinite_generator.py &
        PIPELINE_PID=$!
        wait "$PIPELINE_PID"
        ;;
    *)
        echo -e "${GREEN}Autonomous Video Worker CLI${NC}"
        echo ""
        echo "Usage: ./run_worker.sh <command>"
        echo ""
        echo "Commands:"
        echo "  start [profile]        Start single pipeline run for profile (default: dark_history)"
        echo "  series [profile] [N]   Launch multi-episode series (default: true_crime, 5 episodes)"
        echo "  infinite               Run continuous autonomous generator across all niches overnight"
        echo "  status                 View active checkpoints, scraper status, and generated videos"
        echo "  profiles               List available niche profiles"
        echo "  clear [profile]        Manually clear a profile checkpoint"
        echo ""
        exit 1
        ;;
esac
