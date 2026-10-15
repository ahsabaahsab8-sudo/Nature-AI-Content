# 🌿 Nature AI Studio

A small web dashboard that turns **one sentence** into a finished short video:

```
brief  →  LLM writes the story plan + N image prompts  →  you drop N images into N boxes
       →  LLM writes the HyperFrames composition  →  lint  →  render  →  preview + download
```

Everything the command-line `agent.py` does, but with a UI, live progress and a
History tab. Reachable from anywhere through a Cloudflare tunnel
(`https://<random>.trycloudflare.com`) — no port forwarding, no public IP.

---

## Quick start (Windows)

```powershell
# 1. one-time: install the Cloudflare tunnel client
winget install --id Cloudflare.cloudflared

# 2. start everything (API + tunnel)
cd "C:\Users\PC\Desktop\Nature AI animation\studio"
.\run.ps1
```

The first run prints a generated dashboard password and then:

```
══════════════════════════════════════════════════════════
  PUBLIC URL   https://something-words-here.trycloudflare.com
══════════════════════════════════════════════════════════
```

Open that URL on your phone or laptop. The same URL is shown in the dashboard
header (click it to copy). Projects land in the repo-root folder, renders land in
`renders/`.

## Quick start (Linux / VPS)

```bash
sudo apt-get install -y python3-venv ffmpeg
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared

cd /srv/nature-ai/studio
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
npm install -g hyperframes          # avoids an npx download on every render

./run.sh
```

### Run it 24/7 as a service

```ini
# /etc/systemd/system/nature-studio.service
[Unit]
Description=Nature AI Studio
After=network-online.target

[Service]
Type=simple
User=studio
WorkingDirectory=/srv/nature-ai/studio
EnvironmentFile=/srv/nature-ai/.env
ExecStart=/srv/nature-ai/studio/.venv/bin/python -m uvicorn app:app \
          --host 127.0.0.1 --port 8787
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Run the tunnel next to it:

```ini
# /etc/systemd/system/nature-tunnel.service
[Service]
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8787
Restart=always
```

```bash
sudo systemctl enable --now nature-studio nature-tunnel
sudo journalctl -u nature-tunnel -f | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com'
```

A **quick tunnel** URL changes every restart — fine for a demo, not for a link you
share. For a permanent hostname, use a named tunnel instead:

```bash
cloudflared tunnel login
cloudflared tunnel create nature-studio
cloudflared tunnel route dns nature-studio studio.example.com
# then replace the ExecStart line with:
#   cloudflared tunnel run nature-studio
```

---

## Using the dashboard

| Step | What happens |
|------|--------------|
| **1 · Describe your video** | Type the brief, pick a duration, hit **Analyse**. The LLM streams its story plan live. |
| **2 · Story plan** | Hook, tone, CTA and the scene-by-scene arc. *Regenerate plan* re-runs PHASE 1. |
| **3 · Image prompts** | One card per image. **Copy prompt** → paste into your image generator. |
| **4 · Add your images** | Exactly one box per prompt. Tap a box to open the gallery, drag-drop, or paste from the clipboard. The bar tracks `n / N`. |
| **5 · Generate code & render** | Enabled only when every box is filled. Shows the LLM writing `index.html`, the lint pass, and the render with a live 0-100 % progress bar. |
| **History** | Every rendered video with inline preview + **Download MP4**. *Import from renders/* picks up videos you made outside the dashboard. |

Progress is streamed over SSE, so a page refresh replays the whole run instead of
losing it. Reload-safe: the project you were working on is restored automatically.

---

## Tests

```bash
cd studio
python test_frontend.py        # static assets
python test_encoding.py        # UTF-8 integrity
python test_cli_bridge.py      # spawns the CLI and renders a 1s probe video

# with the studio running (start it first):
python test_sse.py             # SSE replay + resume + live delivery
python smoke_test.py           # the whole pipeline, ~5 min (2 LLM calls + a real render)
```

`smoke_test.py` uploads the images from an existing project as stand-ins, so you
can exercise the full flow without generating any art.

---

## Configuration

Everything lives in the repo-root `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `KILOCODE_API_KEY` | — | **Required.** LLM key. |
| `KILOCODE_BASE_URL` | `https://api.kilo.ai/api/gateway` | Any OpenAI-compatible endpoint. |
| `KILOCODE_MODEL` | `stepfun/step-5-preview-free` | Planner + code writer. |
| `MAX_TOKENS` | `32000` | Per-response ceiling. |
| `STUDIO_PASSWORD` | *(empty)* | Dashboard password. **Set this before exposing a VPS.** |
| `STUDIO_PORT` | `8787` | Local API port (the tunnel points here). |
| `STUDIO_PROJECTS_DIR` | repo root | Project folders + `assets/`. |
| `STUDIO_RENDERS_DIR` | `<repo>/renders` | Finished MP4s. |
| `STUDIO_HYPERFRAMES` | `npx --yes hyperframes` | Point at a global install: `hyperframes`. |
| `STUDIO_RENDERS_DIR`… `STUDIO_MAX_UPLOAD_MB` | `40` | Upload ceiling per image. |
| `STUDIO_RENDER_TIMEOUT` | `3600` | Seconds before a render is killed. |

---

## How it fits together

```
studio/
  app.py         FastAPI routes · SSE live feed · range-request video serving · auth
  pipeline.py    LLM phases, hyperframes lint/render orchestration, progress parsing
  parsing.py     PHASE-1 answer → story plan + image slots; LLM reply → index.html
  prompts.py     PHASE-1 manifest contract + PHASE-2/3 system prompt
  store.py       SQLite: projects, slots, chat history, events, videos
  config.py      .env loader + HyperFrames CLI resolution
  static/        the dashboard (vanilla HTML/CSS/JS, no build step)
  run.ps1 / run.sh   server + Cloudflare quick tunnel launchers

  # checks (run from studio/; the first three need a running server)
  smoke_test.py      full E2E: brief -> prompts -> uploads -> code -> lint -> render
  test_sse.py        SSE replay, Last-Event-Id resume, live event delivery
  test_cli_bridge.py CLI quoting + progress parsing (no server needed)
  test_frontend.py   static HTML/CSS/JS consistency
  test_encoding.py   mojibake guard on the static assets
```

Design notes worth knowing:

- **Image manifest is the contract.** The LLM must emit `IMAGE n - file.png`
  blocks. The parser is forgiving (bold headers, missing fields, prose-only
  answers all degrade gracefully) and every entry becomes one upload box, so the
  box count always matches the prompt count.
- **Filenames are the link.** Each slot stores the image under the exact filename
  the manifest declared, and the code-generation phase hands the model an
  explicit `ASSET MAP` of only those paths, so `src="assets/…"` can never point
  at a file that does not exist.
- **Progress is parsed, not guessed.** HyperFrames output is read for `n/m` and
  `%` signals, then mapped onto one 0-100 bar across setup → capture → encode.
- **Everything is replayable.** Events are stored in SQLite; the SSE endpoint
  replays the backlog before streaming live, so refreshes and flaky tunnel
  connections never lose the run.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cloudflared not found` | `winget install --id Cloudflare.cloudflared` (or the apt steps above). |
| Render fails on a fresh VPS | First run downloads `chrome-headless-shell`. Check with `npx hyperframes doctor`. |
| `ffmpeg not found` | HyperFrames needs ffmpeg **and** ffprobe on PATH. |
| Render is very slow | Each worker is a Chrome process (~256 MB). Lower cores with `--workers 2`, or keep `--no-browser-gpu` on headless boxes. |
| Disk fills up | Rendered MP4s are large. Prune `renders/`; the History tab can delete them. |
| LLM says `bad parameter or other API misuse` | Gateway hiccup — the pipeline retries 4× automatically. Swap `KILOCODE_BASE_URL`/`KILOCODE_MODEL` if it persists. |
| Video preview stutters through the tunnel | Quick tunnels are not fast lanes. A named tunnel (or serving the file from object storage) fixes it. |
| Dashboard reachable but empty after restart | Jobs do not survive a restart; the project flips to **interrupted** and you just hit render again. |

---

## Security notes

- The dashboard is reachable by anyone holding the URL — **always set
  `STUDIO_PASSWORD`** on a public box. Sessions are HMAC-signed, HttpOnly cookies.
- The server binds `127.0.0.1`, so it is only ever exposed through the tunnel.
- Rendering executes code written by an LLM inside Chrome. Keep the VPS a
  throwaway box you can rebuild, not a machine with anything precious on it.