#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# NetBox-Twenty Middleware - Test Environment Teardown
# ═══════════════════════════════════════════════════════════════
# This script stops and optionally removes all test environment
# resources including volumes.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.test.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# ───────────────────────────────────────────────────────────────
# Parse arguments
# ───────────────────────────────────────────────────────────────
REMOVE_VOLUMES=false
REMOVE_IMAGES=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--volumes)
            REMOVE_VOLUMES=true
            shift
            ;;
        -i|--images)
            REMOVE_IMAGES=true
            shift
            ;;
        -a|--all)
            REMOVE_VOLUMES=true
            REMOVE_IMAGES=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -v, --volumes   Remove volumes (database data)"
            echo "  -i, --images    Remove built images"
            echo "  -a, --all       Remove volumes and images"
            echo "  -h, --help      Show this help message"
            exit 0
            ;;
        *)
            log_warn "Unknown option: $1"
            shift
            ;;
    esac
done

# ───────────────────────────────────────────────────────────────
# Main teardown
# ───────────────────────────────────────────────────────────────
main() {
    log_info "Stopping test environment..."

    local compose_args=("-f" "$COMPOSE_FILE")

    if [ "$REMOVE_VOLUMES" = true ]; then
        log_warn "Removing volumes (all data will be lost)..."
        compose_args+=("--volumes")
    fi

    docker compose "${compose_args[@]}" down

    if [ "$REMOVE_IMAGES" = true ]; then
        log_info "Removing built images..."
        docker compose "${compose_args[@]}" down --rmi local 2>/dev/null || true
    fi

    echo ""
    log_success "Test environment stopped"
    echo ""

    if [ "$REMOVE_VOLUMES" = false ]; then
        echo -e "${YELLOW}Note:${NC} Data volumes were preserved."
        echo "To remove all data, run: $0 --volumes"
        echo ""
    fi

    echo -e "${BLUE}To restart:${NC}"
    echo "  ./tests/setup-test-env.sh"
    echo ""
}

main
