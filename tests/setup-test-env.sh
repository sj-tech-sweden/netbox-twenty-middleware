#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# NetBox-Twenty Middleware - Test Environment Setup
# ═══════════════════════════════════════════════════════════════
# This script sets up a complete test environment with:
#   - NetBox (http://localhost:8100)
#   - Twenty CRM (http://localhost:3100)
#   - Middleware API (http://localhost:8001)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.test.yml"
ENV_FILE="$PROJECT_DIR/.env.test"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ───────────────────────────────────────────────────────────────
# Pre-flight checks
# ───────────────────────────────────────────────────────────────
preflight_checks() {
    log_info "Running pre-flight checks..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi

    if ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not available. Please update Docker."
        exit 1
    fi

    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "docker-compose.test.yml not found at $COMPOSE_FILE"
        exit 1
    fi

    log_success "Pre-flight checks passed"
}

# ───────────────────────────────────────────────────────────────
# Create .env.test if it doesn't exist
# ───────────────────────────────────────────────────────────────
setup_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        log_info "Creating .env.test from template..."
        cp "$PROJECT_DIR/.env.test.example" "$ENV_FILE"

        # Generate secrets
        NETBOX_WEBHOOK_SECRET=$(openssl rand -hex 32)
        TWENTY_WEBHOOK_TOKEN=$(openssl rand -hex 32)

        # Update the .env.test with generated values
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/^# NETBOX_WEBHOOK_SECRET=.*/NETBOX_WEBHOOK_SECRET=$NETBOX_WEBHOOK_SECRET/" "$ENV_FILE"
            sed -i '' "s/^# TWENTY_WEBHOOK_TOKEN=.*/TWENTY_WEBHOOK_TOKEN=$TWENTY_WEBHOOK_TOKEN/" "$ENV_FILE"
        else
            sed -i "s/^# NETBOX_WEBHOOK_SECRET=.*/NETBOX_WEBHOOK_SECRET=$NETBOX_WEBHOOK_SECRET/" "$ENV_FILE"
            sed -i "s/^# TWENTY_WEBHOOK_TOKEN=.*/TWENTY_WEBHOOK_TOKEN=$TWENTY_WEBHOOK_TOKEN/" "$ENV_FILE"
        fi

        log_success "Created .env.test with auto-generated secrets"
    else
        log_info "Using existing .env.test"
    fi
}

# ───────────────────────────────────────────────────────────────
# Start Docker Compose stack
# ───────────────────────────────────────────────────────────────
start_stack() {
    log_info "Starting test environment..."
    docker compose -f "$COMPOSE_FILE" up -d

    log_info "Waiting for services to become healthy..."
    local max_wait=600
    local elapsed=0

    while [ $elapsed -lt $max_wait ]; do
        local unhealthy=$(docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null | \
            grep -c '"Health":"starting"' || true)

        if [ "$unhealthy" -eq 0 ]; then
            break
        fi

        echo -ne "\r${YELLOW}Waiting... ${elapsed}s/${max_wait}s (services starting: ${unhealthy})${NC}  "
        sleep 5
        elapsed=$((elapsed + 5))
    done
    echo ""

    if [ $elapsed -ge $max_wait ]; then
        log_warn "Some services may still be starting. Check with: docker compose -f $COMPOSE_FILE ps"
    fi

    # NetBox performs initial database migrations on first start, which can
    # take several minutes. Wait until the API responds before continuing.
    log_info "Waiting for NetBox API to be ready (this may take a few minutes on first start)..."
    elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        if curl -sf http://localhost:8100/api/status/ >/dev/null 2>&1; then
            break
        fi
        echo -ne "\r${YELLOW}Waiting for NetBox API... ${elapsed}s/${max_wait}s${NC}  "
        sleep 5
        elapsed=$((elapsed + 5))
    done
    echo ""

    if [ $elapsed -ge $max_wait ]; then
        log_warn "NetBox API did not become ready in time. Check with: docker compose -f $COMPOSE_FILE logs netbox"
    else
        log_success "NetBox API is ready"
    fi

    log_success "Stack started"
}

# ───────────────────────────────────────────────────────────────
# Create NetBox superuser and API token
# ───────────────────────────────────────────────────────────────
setup_netbox() {
    log_info "Setting up NetBox..."

    # Create superuser
    docker compose -f "$COMPOSE_FILE" exec -T netbox /opt/netbox/netbox/manage.py shell -c "
from django.contrib.auth import get_user_model;
User = get_user_model();
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@test.local', 'admin');
    print('Created superuser: admin/admin')
else:
    print('Superuser already exists')
" 2>/dev/null || log_warn "Could not create NetBox superuser (may already exist)"

    # Create API token (NetBox v4.7+ uses hashed v2 tokens)
    local token_output
    token_output=$(docker compose -f "$COMPOSE_FILE" exec -T netbox /opt/netbox/netbox/manage.py shell -c "
from users.models import Token;
try:
    token = Token.objects.create(user_id=1, write_enabled=True, description='Test Token');
    print(f'nbt_{token.key}.{token.token}');
except Exception as e:
    print(str(e));
" 2>/dev/null || echo "")

    # Validate the token output looks like a v2 token: nbt_<12-char-key>.<40-char-secret>
    if [[ "$token_output" =~ ^nbt_[A-Za-z0-9]+\.[A-Za-z0-9]{40}$ ]]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^NETBOX_TOKEN=.*|NETBOX_TOKEN=$token_output|" "$ENV_FILE"
        else
            sed -i "s|^NETBOX_TOKEN=.*|NETBOX_TOKEN=$token_output|" "$ENV_FILE"
        fi
        log_success "NetBox API token configured"

        # Middleware must be recreated to pick up the new token from env_file
        log_info "Restarting middleware services with new NetBox token..."
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate middleware-api middleware-worker
    else
        log_warn "Could not auto-generate NetBox token. Create one manually in the UI."
        log_warn "Output was: $token_output"
    fi
}

# ───────────────────────────────────────────────────────────────
# Print summary and instructions
# ───────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Test Environment Setup Complete!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${BLUE}NetBox:${NC}      http://localhost:8100"
    echo -e "                 Username: ${YELLOW}admin${NC}"
    echo -e "                 Password: ${YELLOW}admin${NC}"
    echo ""
    echo -e "  ${BLUE}Twenty CRM:${NC}  http://localhost:3100"
    echo -e "                 (Complete first-time setup in browser)"
    echo ""
    echo -e "  ${BLUE}Middleware:${NC}  http://localhost:8001"
    echo -e "                 Health:   http://localhost:8001/v1/healthz"
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${YELLOW}Next Steps:${NC}"
    echo ""
    echo "  1. Open Twenty CRM at http://localhost:3100"
    echo "     - Create your admin account"
    echo "     - Go to Settings > API Keys"
    echo "     - Create a new API key"
    echo ""
    echo "  2. Update .env.test with the Twenty API key:"
    echo "     TWENTY_API_KEY=<your-api-key>"
    echo ""
    echo "  3. Restart the middleware services:"
    echo "     docker compose -f $COMPOSE_FILE restart middleware-api middleware-worker"
    echo ""
    echo "  4. Test the integration:"
    echo "     - Create a Company in Twenty → should create Tenant in NetBox"
    echo "     - Create a Tenant in NetBox → should sync to Twenty"
    echo ""
    echo -e "${YELLOW}Useful Commands:${NC}"
    echo ""
    echo "  View logs:       docker compose -f $COMPOSE_FILE logs -f"
    echo "  View middleware:  docker compose -f $COMPOSE_FILE logs -f middleware-api middleware-worker"
    echo "  Stop stack:      ./tests/teardown-test-env.sh"
    echo ""
}

# ───────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────
main() {
    preflight_checks
    setup_env_file
    start_stack
    setup_netbox
    print_summary
}

main "$@"
