# 🌿 Nature AI Content

A self-hosted web dashboard that turns **one sentence** into a finished short video:

```
brief → LLM writes the story plan + N image prompts → you drop N images into N boxes
      → LLM writes the HyperFrames composition → lint → render → preview + download
```

Everything runs on one box: FastAPI + SQLite + the HyperFrames CLI, published to the
internet through a Cloudflare quick tunnel (`https://*.trycloudflare.com`).

---

## Deploy on a VPS (Ubuntu/Debian)

```bash
git clone https://github.com/ahsabaahsab8/Nature-AI-Content.git nature-ai
cd nature-ai
chmod +x deploy.sh
sudo ./deploy.sh
```

`deploy.sh` installs python3 + ffmpeg + nodejs, the HyperFrames CLI, cloudflared and
the Python deps, then starts both the API and the tunnel with **nohup** — closing
SSH will not stop it. It prints your public URL and the dashboard password when done.

<details>
<summary>Manual install (if you'd rather do it step by step)</summary>

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ffmpeg curl ca-certificates gnupg

# nodejs (needed for the HyperFrames CLI)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g hyperframes

# cloudflared
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared

# python deps
python3 -m venv .venv
.venv/bin/pip install -r studio/requirements.txt
```

Then run both halves in the background:

```bash
cd studio
nohup ../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8787 \
      >> logs/api.log 2>&1 &
nohup cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8787 \
      >> logs/cloudflared.log 2>&1 &

# the tunnel URL
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/cloudflared.log | head -1
```
</details>

---

## Using the dashboard

| Step | What happens |
|------|--------------|
| **1 · Describe your video** | Type the brief, pick a duration, hit **Analyse**. The LLM streams its plan live. |
| **2 · Story plan** | Hook, tone, CTA and the scene-by-scene arc. *Regenerate plan* re-runs PHASE 1. |
| **3 · Image prompts** | One card per image. **Copy prompt** → paste into your image generator. |
| **4 · Add your images** | Exactly one box per prompt. Tap a box to open the gallery, drag-drop, or paste from the clipboard. |
| **5 · Generate code & render** | Enabled only when every box is filled. Live 0–100 % progress + console. |
| **History** | Every rendered video with inline preview + **Download MP4**. *Import from renders/* picks up videos made outside the dashboard. |

Projects are written to `./<project-slug>/` and MP4s to `./renders/`.

---

## Layout

```
.env                   KiloCode key + dashboard password (per-machine settings)
SYSTEM_INSTRUCTIONS.md the HyperFrames/GSAP rules the code-generation phase obeys
deploy.sh              one-shot VPS setup + nohup launcher
studio/
  app.py               FastAPI routes · SSE live feed · range-request video serving · auth
  pipeline.py          LLM phases, lint/render orchestration, progress parsing
  parsing.py           PHASE-1 answer → story plan + image slots; LLM reply → index.html
  prompts.py           manifest contract + code-generation system prompt
  store.py             SQLite: projects, slots, chat history, events, videos
  config.py            .env loader + HyperFrames CLI resolution
  static/              the dashboard (vanilla HTML/CSS/JS, no build step)
  run.sh / run.ps1     interactive launcher (also opens the tunnel)
  requirements.txt
  test_*.py, smoke_test.py
renders/               finished MP4s (git-ignored)
```

Full documentation: **[studio/README.md](studio/README.md)**.

---

## ⚠️ Security

- **`.env` is committed in this repo and contains a live `KILOCODE_API_KEY`.** Because the repo is public, anyone can read and spend that key. Rotate it as soon as you can, or move it out of git and set `KILOCODE_API_KEY` in the VPS environment instead.
- Set `STUDIO_PASSWORD` before exposing the box. Sessions are HMAC-signed, HttpOnly cookies; the API binds `127.0.0.1` so it is only reachable through the tunnel.
- Rendering executes LLM-authored code inside Chrome. Keep it on a throwaway box, not a machine with anything precious on it.
- A quick-tunnel URL changes on every restart. Use a [named tunnel](studio/README.md#named-tunnel-for-a-permanent-url) for a stable hostname.