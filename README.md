# 🌿 Nature AI Content Studio — Autonomous Short-Form Video Generator 🎬

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Cloudflare](https://img.shields.io/badge/Cloudflare-Tunnel-F38020?logo=cloudflare&logoColor=white)](https://cloudflare.com)
[![HyperFrames](https://img.shields.io/badge/Renderer-HyperFrames%20CLI-FF4081)](https://github.com)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> 🚀 **Transform a single sentence or prompt into a high-converting, rendered cinematic short-form video in seconds.**  
> Powered by LLM story planning, automated image slotting, HyperFrames code-gen (GSAP animations), headless rendering, and zero-config Cloudflare Tunnels.

---

## 🌟 Overview & Key Capabilities

**Nature AI Content Studio** is a production-grade, self-hosted web studio built for automated video creation pipelines:

* 📝 **Automated Storyboard & Hook Engine**: Turns short prompts into narrative arcs with retention hooks, scene pacing, and Calls-to-Action (CTA).
* 🎨 **Multi-Slot Image Prompter**: Generates per-scene Midjourney/Flux/SD prompts with 1-click clipboard copy and drag-and-drop asset matching.
* ⚡ **HyperFrames & GSAP Rendering**: Synthesizes reactive web animations, dynamic text layers, camera pans, and transitions rendered directly to high-bitrate MP4.
* 🌐 **Instant Cloudflare Quick Tunnel**: Exposes your local instance or VPS securely over HTTPS (`https://*.trycloudflare.com`) with zero port-forwarding.
* 🔒 **Secured Dashboard**: Password protection with HTTP-only signed session tokens and live Server-Sent Events (SSE) progress bars.

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  Prompt / Brief │ ───>  │ LLM Story Plan  │ ───>  │ Image Prompting │
└─────────────────┘       └─────────────────┘       └────────┬────────┘
                                                             │
┌─────────────────┐       ┌─────────────────┐       ┌────────▼────────┐
│  MP4 Video Out  │ <───  │ HyperFrames CLI │ <───  │  Asset Drop &   │
│ (1080x1920 60f) │       │ GSAP Rendering  │       │  Code Synthesis │
└─────────────────┘       └─────────────────┘       └─────────────────┘
```

---

## 🛠️ Tech Stack

| Component | Technology | Description |
| :--- | :--- | :--- |
| **Backend API** | FastAPI / Uvicorn | Async REST endpoints, SSE streams, range-request MP4 video delivery |
| **Database** | SQLite3 | Embedded persistence for projects, scene slots, prompt state, and renders |
| **Rendering Engine** | HyperFrames CLI + Puppeteer | Headless Chrome video compositor executing JavaScript GSAP animations |
| **LLM Gateway** | KiloCode API / OpenAI-compatible | Generates scene scripts, camera movements, and HTML5 canvas code |
| **Networking** | Cloudflare Tunnel (`cloudflared`) | Secure ingress without opening firewall ports or public IP exposure |
| **Frontend UI** | Modern Vanilla JS + CSS Glassmorphism | Zero-build fast dashboard with drag-and-drop media uploaders |

---

## 🔑 Required Environment Variables (`.env`)

Create a `.env` file in the project root by copying `.env.example`:

```bash
cp .env.example .env
```

| Variable | Required | Description | Example / Default |
| :--- | :---: | :--- | :--- |
| `KILOCODE_API_KEY` | **Yes** | API key for LLM code generation (KiloCode or OpenAI format) | `your_api_key_here` |
| `KILOCODE_BASE_URL` | No | Base API endpoint for the LLM gateway | `https://api.kilo.ai/api/gateway` |
| `KILOCODE_MODEL` | No | Target LLM model for scriptwriting and code generation | `stepfun/step-5-preview-free` |
| `STUDIO_PASSWORD` | **Yes** | Web UI login password to prevent unauthorized generation | `YourSuperSecurePassword123!` |
| `PROJECTS_DIR` | No | Local folder where intermediate assets and code reside | `./renders` |
| `DEFAULT_WIDTH` | No | Horizontal video resolution in pixels | `1080` (9:16 portrait) |
| `DEFAULT_HEIGHT` | No | Vertical video resolution in pixels | `1920` (Shorts / Reels / TikTok) |
| `DEFAULT_FPS` | No | Rendered video frame rate | `30` or `60` |

---

## 🚀 Quick Start: Deployment on a Linux VPS (Ubuntu / Debian)

### Option 1: One-Click Automated Script (`deploy.sh`)

Run the automated installer on any fresh Ubuntu/Debian server:

```bash
# 1. Clone the repository
git clone https://github.com/ahsabaahsab8-sudo/Nature-AI-Content.git
cd Nature-AI-Content

# 2. Configure your environment
cp .env.example .env
nano .env   # Enter your KILOCODE_API_KEY and STUDIO_PASSWORD

# 3. Execute setup & background daemon
chmod +x deploy.sh
sudo ./deploy.sh
```

`deploy.sh` automatically provisions:
1. `python3-venv`, `ffmpeg`, `nodejs 22.x`, and `cloudflared`
2. Installs global `hyperframes` CLI via npm
3. Builds virtual environment and installs dependencies
4. Launches both Uvicorn and Cloudflare Tunnel in the background with health monitors

---

### Option 2: Production `systemd` Service Setup

For resilient 24/7 background operation with auto-restart on crashes or reboots:

1. **Create the systemd service file**:
   ```bash
   sudo nano /etc/systemd/system/nature-ai-studio.service
   ```

2. **Add configuration**:
   ```ini
   [Unit]
   Description=Nature AI Content Studio Daemon
   After=network.target

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/opt/Nature-AI-Content/studio
   EnvironmentFile=/opt/Nature-AI-Content/.env
   ExecStart=/opt/Nature-AI-Content/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8787
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable & Start**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now nature-ai-studio
   sudo systemctl status nature-ai-studio
   ```

---

## 🖥️ Using the Studio Dashboard

1. **Step 1 — Video Brief**: Enter your topic (e.g. *"Top 3 bioluminescent plants and how they glow at night"*), set target duration (15s–60s), and hit **Analyze**.
2. **Step 2 — Narrative Plan**: The AI generates a 3-part storyline (Hook, Body, CTA) with visual cues and timeframes.
3. **Step 3 — Scene Slots & Prompts**: Copy optimized prompts for Midjourney / Stable Diffusion directly from the cards.
4. **Step 4 — Drag & Drop**: Drop matching images into the corresponding scene slots.
5. **Step 5 — Generate & Render**: Click **Synthesize Video**. Watch the real-time build log compile the animations and render the final MP4.
6. **Step 6 — Preview & Export**: Download the MP4 ready for YouTube Shorts, Instagram Reels, and TikTok!

---

## 📂 Project Architecture

```
Nature-AI-Content/
├── .env.example          # Environment blueprint with placeholders
├── deploy.sh             # Zero-touch Linux VPS automated deployment script
├── doctor.sh             # Diagnostic script for checking dependencies & tunnels
├── SYSTEM_INSTRUCTIONS.md# GSAP & HyperFrames code generation rules
├── renders/              # Output directory for compiled MP4 videos
└── studio/
    ├── app.py            # FastAPI REST & SSE controller
    ├── pipeline.py       # Orchestration pipeline for code synthesis & rendering
    ├── parsing.py        # Parser for LLM stream payloads & HTML5 extraction
    ├── prompts.py        # System prompts and schema specifications
    ├── store.py          # SQLite persistence layer
    ├── config.py         # App configuration & environment loader
    └── static/           # Glassmorphism frontend UI (HTML5/CSS3/Vanilla JS)
```

---

## 🛡️ Security & Safe Usage

* **Never commit real API keys**: Ensure `.env` is listed in your `.gitignore` before pushing changes.
* **Keep `STUDIO_PASSWORD` secure**: Any visitor to your Cloudflare URL can generate renders if authentication is disabled.
* **Resource Monitoring**: Video rendering with Puppeteer uses CPU/RAM spikes; a VPS with at least 2 vCPUs and 4GB RAM is recommended.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more details.
