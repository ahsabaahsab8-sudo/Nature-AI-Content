#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Nature AI Content — post-install health check.
#
#      ./doctor.sh
#
#  Verifies everything the pipeline needs BEFORE you spend a real task on it:
#  .env, the LLM key, the HyperFrames CLI, the Node version, ffmpeg, Chrome and
#  the API itself. Exit code 0 = healthy.
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_DIR="$APP_DIR/studio"
PY="$APP_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
PORT="${STUDIO_PORT:-8787}"
FAIL=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=1; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

head_ "1. .env"
if [ -f "$APP_DIR/.env" ]; then
  ok "$APP_DIR/.env exists"
  for key in KILOCODE_API_KEY KILOCODE_BASE_URL KILOCODE_MODEL STUDIO_PASSWORD; do
    val="$(grep -m1 "^${key}=" "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)"
    [ -n "$val" ] && ok "$key is set (${#val} chars)" || bad "$key is EMPTY or missing"
  done
else
  bad "no .env at $APP_DIR/.env"
fi

head_ "2. Python environment"
if [ -x "$APP_DIR/.venv/bin/python" ]; then
  ok "venv python: $("$PY" --version 2>&1)"
  for mod in fastapi uvicorn openai multipart; do
    "$PY" -c "import $mod" 2>/dev/null && ok "$mod installed" || bad "$mod MISSING (pip install -r studio/requirements.txt)"
  done
else
  bad "no venv at $APP_DIR/.venv — run deploy.sh"
fi

head_ "3. Node + HyperFrames"
NODE_MAJOR=0
command -v node >/dev/null 2>&1 && NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -ge 22 ]; then
  ok "node $(node -v)"
else
  bad "node $(node -v 2>/dev/null || echo missing) — HyperFrames needs >= 22 (curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs)"
fi
if command -v hyperframes >/dev/null 2>&1; then
  HFV="$(hyperframes --version 2>&1 | head -1)"
  case "$HFV" in
    *"requires Node"*) bad "hyperframes present but will not run: $HFV" ;;
    *) ok "hyperframes $HFV" ;;
  esac
else
  bad "hyperframes not installed (sudo npm install -g hyperframes)"
fi

head_ "4. ffmpeg / ffprobe / Chrome"
command -v ffmpeg  >/dev/null 2>&1 && ok "ffmpeg $(ffmpeg -version | head -1 | cut -d' ' -f3)" || bad "ffmpeg missing (sudo apt-get install -y ffmpeg)"
command -v ffprobe >/dev/null 2>&1 && ok "ffprobe present" || bad "ffprobe missing — renders fail without it"
CACHE="${HOME}/.cache/hyperframes/chrome"
if [ -d "$CACHE" ]; then
  ok "headless Chrome cached ($(du -sh "$CACHE" 2>/dev/null | cut -f1))"
else
  warn "Chrome not downloaded yet — the first render fetches ~150 MB, allow extra time"
fi

head_ "5. LLM reachability"
"$PY" - <<'PYEOF' 2>&1 | sed 's/^/  /'
import os, sys
from pathlib import Path

env = Path(os.environ["APP_DIR"]) / ".env" if "APP_DIR" in os.environ else None
if env is None:
    sys.path.insert(0, os.path.join(os.getcwd(), "studio"))
    try:
        from config import settings
    except Exception as exc:
        print(f"✗ cannot load config: {exc}")
        sys.exit(1)
    key, base, model = settings.api_key, settings.base_url, settings.model
else:
    vals = {}
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()
    key = vals.get("KILOCODE_API_KEY", "")
    base = vals.get("KILOCODE_BASE_URL", "https://api.kilo.ai/api/gateway")
    model = vals.get("KILOCODE_MODEL", "stepfun/step-5-preview-free")

print(f"  model: {model}")
if not key:
    print("  ✗ KILOCODE_API_KEY is empty")
    sys.exit(1)

from openai import OpenAI
try:
    client = OpenAI(api_key=key, base_url=base, timeout=45)
    reply = client.chat.completions.create(
        model=model, max_tokens=50,
        messages=[{"role": "user", "content": "Say OK"}],
    ).choices[0].message.content
    print(f"  ✓ LLM answered: {reply}")
except Exception as exc:
    text = str(exc)
    if "429" in text or "rate" in text.lower():
        print("  ✗ LLM RATE LIMITED (quota exhausted or throttled)")
        print(f"    {text[:220]}")
        print("    fix: switch KILOCODE_MODEL to another model, or wait for the quota window")
    elif "401" in text:
        print("  ✗ LLM rejected the key (401)")
    elif "Connection" in text or "resolve" in text.lower():
        print("  ✗ VPS cannot reach the LLM endpoint (network/DNS)")
    else:
        print(f"  ✗ {type(exc).__name__}: {text[:220]}")
    sys.exit(1)
PYEOF
[ $? -ne 0 ] && FAIL=1

head_ "6. API + tunnel"
if curl -fsS --max-time 8 "http://127.0.0.1:$PORT/api/session" >/dev/null 2>&1; then
  ok "API responding on 127.0.0.1:$PORT"
else
  bad "API not responding — start it: cd studio && nohup ../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port $PORT >> logs/api.log 2>&1 &"
fi
if pgrep -f 'cloudflared tunnel' >/dev/null 2>&1; then
  ok "cloudflared running"
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$STUDIO_DIR/logs/cloudflared.log" 2>/dev/null | tail -1)"
  [ -n "$URL" ] && ok "current URL: $URL" || warn "no URL in the log yet"
  curl -fsS --max-time 12 "$URL/api/session" >/dev/null 2>&1 \
    && ok "$URL answers" \
    || warn "$URL did not answer — Error 1033 means the connector is down; restart cloudflared"
else
  warn "cloudflared not running"
fi

head_ "7. Last job errors"
"$PY" - <<'PYEOF' 2>&1 | sed 's/^/  /'
import sqlite3, pathlib
db = pathlib.Path("studio/studio.db")
if not db.exists():
    print("(no database yet)")
else:
    rows = sqlite3.connect(db).execute(
        "SELECT brief,status,error FROM projects ORDER BY created_at DESC LIMIT 3"
    ).fetchall()
    if not rows:
        print("(no projects yet)")
    for brief, status, error in rows:
        print(f"· [{status}] {brief[:52]}")
        if error:
            print(f"    {error[:200]}")
PYEOF

printf '\n'
if [ "$FAIL" = "0" ]; then
  printf '\033[32m● healthy — ready to create a project\033[0m\n\n'
else
  printf '\033[31m● problems found above — fix them before rendering\033[0m\n\n'
fi
exit "$FAIL"