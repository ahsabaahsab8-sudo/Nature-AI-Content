#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Nature AI Content — one-shot VPS setup + 24/7 background launch.
#
#    git clone <repo-url> nature-ai && cd nature-ai
#    chmod +x deploy.sh && sudo ./deploy.sh
#
#  Installs every dependency, then starts the API and the Cloudflare tunnel
#  with nohup so they survive logout. Safe to re-run (it restarts cleanly).
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_DIR="$APP_DIR/studio"
LOG_DIR="$STUDIO_DIR/logs"
PORT="${STUDIO_PORT:-8787}"
RUN_AS="${SUDO_USER:-$(id -un)}"

say()  { printf '\n\033[36m▸ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$APP_DIR/.env" ] || die "No .env found. The dashboard needs KILOCODE_API_KEY."

# ── 1. system packages ─────────────────────────────────────────────────────
say "Installing system packages (python3, ffmpeg, nodejs)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg curl ca-certificates gnupg >/dev/null
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
node --version && ffmpeg -version | head -1

# ── 2. HyperFrames CLI ─────────────────────────────────────────────────────
say "Installing the HyperFrames CLI..."
if command -v hyperframes >/dev/null 2>&1; then
  echo "  already installed: $(hyperframes --version)"
else
  npm install -g hyperframes
fi

# ── 3. Cloudflare tunnel client ────────────────────────────────────────────
say "Installing cloudflared..."
if command -v cloudflared >/dev/null 2>&1; then
  echo "  already installed: $(cloudflared --version)"
else
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq && apt-get install -y -qq cloudflared
fi

# ── 4. Python environment ──────────────────────────────────────────────────
say "Creating the Python environment..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$STUDIO_DIR/requirements.txt"
echo "  installed: $("$APP_DIR/.venv/bin/pip" list --format=freeze | wc -l) packages"

# ── 5. stop anything already running ───────────────────────────────────────
say "Stopping any previous instance..."
pkill -f 'uvicorn app:app' 2>/dev/null || true
pkill -f 'cloudflared tunnel' 2>/dev/null || true
sleep 1

mkdir -p "$LOG_DIR"
rm -f "$STUDIO_DIR/public_url.txt"

# ── 6. launch the API in the background ────────────────────────────────────
say "Starting the API on 127.0.0.1:$PORT (nohup)..."
cd "$STUDIO_DIR"
nohup "$APP_DIR/.venv/bin/python" -m uvicorn app:app \
      --host 127.0.0.1 --port "$PORT" --log-level info \
      >> "$LOG_DIR/api.log" 2>&1 &
echo $! > "$LOG_DIR/api.pid"

for _ in $(seq 1 40); do
  sleep 0.5
  if curl -fsS "http://127.0.0.1:$PORT/api/session" >/dev/null 2>&1; then break; fi
  kill -0 "$(cat "$LOG_DIR/api.pid")" 2>/dev/null || { tail -20 "$LOG_DIR/api.log"; die "API failed to start"; }
done
curl -fsS "http://127.0.0.1:$PORT/api/session" >/dev/null || { tail -20 "$LOG_DIR/api.log"; die "API never became ready"; }
echo "  API is live"

# ── 7. launch the Cloudflare quick tunnel in the background ────────────────
say "Starting the Cloudflare quick tunnel (nohup)..."
nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" \
      >> "$LOG_DIR/cloudflared.log" 2>&1 &
echo $! > "$LOG_DIR/tunnel.pid"

PUBLIC_URL=""
for _ in $(seq 1 60); do
  sleep 0.5
  PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/cloudflared.log" 2>/dev/null | head -1 || true)"
  [ -n "$PUBLIC_URL" ] && break
  kill -0 "$(cat "$LOG_DIR/tunnel.pid")" 2>/dev/null || { tail -20 "$LOG_DIR/cloudflared.log"; die "Tunnel failed to start"; }
done
[ -n "$PUBLIC_URL" ] || die "No trycloudflare URL appeared — check $LOG_DIR/cloudflared.log"
printf '%s' "$PUBLIC_URL" > "$STUDIO_DIR/public_url.txt"

# ── 8. done ────────────────────────────────────────────────────────────────
PASSWORD="$(grep -m1 '^STUDIO_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2- || true)"
say "Ready."
printf '\n══════════════════════════════════════════════════════════\n'
printf '  PUBLIC URL   %s\n' "$PUBLIC_URL"
printf '  PASSWORD     %s\n' "${PASSWORD:-(none — dashboard is open)}"
printf '══════════════════════════════════════════════════════════\n\n'
cat <<'EOF'
Running in the background — closing this SSH session will NOT stop it.

  logs      tail -f studio/logs/api.log
  stop      pkill -f 'uvicorn app:app'; pkill -f 'cloudflared tunnel'
  url       cat studio/public_url.txt

NOTE: a quick-tunnel URL changes on every restart. For a permanent hostname,
      see the "Named tunnel" section of studio/README.md.
EOF