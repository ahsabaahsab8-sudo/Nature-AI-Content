#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Nature AI Studio launcher (Linux / VPS)
#
#  Starts the FastAPI dashboard on 127.0.0.1 and opens a Cloudflare quick
#  tunnel, printing the public https://*.trycloudflare.com URL.
#
#    ./run.sh                 # normal start
#    PORT=9000 ./run.sh       # different local port
#    ./run.sh --no-tunnel     # local only
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

STUDIO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$STUDIO_DIR"

NO_TUNNEL=0
TUNNEL_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-tunnel) NO_TUNNEL=1 ;;
    --tunnel-only) TUNNEL_ONLY=1 ;;
  esac
done

REPO_ROOT="$(dirname "$STUDIO_DIR")"
ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "✗ Missing $ENV_FILE — create it and fill in KILOCODE_API_KEY." >&2
  exit 1
fi

# ── ensure a dashboard password exists ────────────────────────────────────
if ! grep -qE '^\s*STUDIO_PASSWORD\s*=\s*\S' "$ENV_FILE"; then
  PW="$(head -c 18 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 18)"
  { echo; echo "# ── Nature AI Studio dashboard ──"; echo "STUDIO_PASSWORD=$PW"; } >> "$ENV_FILE"
  echo -e "Generated a dashboard password:\n    $PW\n" >&2
fi

PORT="${PORT:-8787}"
export STUDIO_PORT="$PORT"
URL_FILE="$STUDIO_DIR/public_url.txt"
LOG_DIR="$STUDIO_DIR/logs"
mkdir -p "$LOG_DIR"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python

API_PID=""
TUNNEL_PID=""
cleanup() {
  echo ""
  echo "▸ stopping…"
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null || true
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  rm -f "$URL_FILE"
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── 1. API server ─────────────────────────────────────────────────────────
if [[ "$TUNNEL_ONLY" == "0" ]]; then
  echo "▸ starting dashboard on 127.0.0.1:$PORT"
  "$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" \
      >"$LOG_DIR/api.log" 2>&1 &
  API_PID=$!

  for _ in $(seq 1 40); do
    sleep 0.5
    if curl -fsS "http://127.0.0.1:$PORT/api/session" >/dev/null 2>&1; then break; fi
    if ! kill -0 "$API_PID" 2>/dev/null; then
      echo "✗ API server failed to start. See $LOG_DIR/api.log" >&2
      tail -n 30 "$LOG_DIR/api.log" >&2 || true
      exit 1
    fi
  done
  echo "✓ dashboard is live on http://127.0.0.1:$PORT"
fi

if [[ "$NO_TUNNEL" == "1" ]]; then
  echo ""
  echo "Local only. Open http://127.0.0.1:$PORT"
  [[ -n "$API_PID" ]] && wait "$API_PID"
  exit 0
fi

# ── 2. Cloudflare quick tunnel ────────────────────────────────────────────
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "✗ cloudflared not found. Install it with:" >&2
  echo "    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null" >&2
  echo "    echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list" >&2
  echo "    sudo apt-get update && sudo apt-get install cloudflared" >&2
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  exit 1
fi

echo "▸ opening Cloudflare quick tunnel…"
rm -f "$URL_FILE"
cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" \
    >"$LOG_DIR/cloudflared.log" 2>&1 &
TUNNEL_PID=$!

PUBLIC=""
for _ in $(seq 1 60); do
  sleep 0.5
  if [[ -f "$LOG_DIR/cloudflared.log" ]]; then
    PUBLIC="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/cloudflared.log" | head -1 || true)"
    if [[ -n "$PUBLIC" ]]; then
      printf '%s' "$PUBLIC" > "$URL_FILE"
      break
    fi
  fi
  kill -0 "$TUNNEL_PID" 2>/dev/null || break
done

echo ""
if [[ -n "$PUBLIC" ]]; then
  echo "══════════════════════════════════════════════════════════"
  echo "  PUBLIC URL   $PUBLIC"
  echo "  (also shown in the dashboard header — click to copy)"
  echo "══════════════════════════════════════════════════════════"
  echo ""
else
  echo "⚠ Tunnel URL not found yet — see $LOG_DIR/cloudflared.log" >&2
fi
echo "Keep this terminal open. Ctrl+C stops the tunnel and the server."

# ── 3. supervise ──────────────────────────────────────────────────────────
wait -n "$API_PID" "$TUNNEL_PID" 2>/dev/null || wait