"""Prompts used by the pipeline.

PHASE 1 emits a machine-parseable image manifest; the dashboard turns every entry
into an upload box. PHASE 2/3 reuse the repo's ``SYSTEM_INSTRUCTIONS.md`` so the
generated composition obeys exactly the same HyperFrames + GSAP rules as the
command-line agent.
"""

from __future__ import annotations

from pathlib import Path

from config import REPO_ROOT

STYLE_SUFFIX = (
    "2D cartoon illustration, flat design with bold outlines, vibrant saturated colors, "
    "cel-shaded, Disney/Nickelodeon TV animation style, clean vector-like shapes, expressive "
    "character faces, no gradients, solid color fills, thick black outlines 3-4px, "
    "no photorealism, no 3D render"
)

PHASE1_SYSTEM = f"""You are the Nature AI Storyboard & Image-Prompt Director for a 2D cartoon \
short-form video factory. This is PHASE 1 of a 3-phase pipeline.

You will receive a VIDEO BRIEF and a target duration. You must:
1. Lock the concept (hook, story arc with beats + durations, CTA, tone, total duration).
2. Emit the COMPLETE, MACHINE-PARSEABLE image manifest for the whole video.

OUTPUT FORMAT — follow it exactly, this is parsed by an automated pipeline:

═══════════════════════════════════
STORY PLAN
═══════════════════════════════════
Then plain text lines:

Hook (0-2s): <what stops the scroll>
Tone: <educational / fear / satisfying / FOMO / funny>
Total Duration: <N> seconds

Story Arc:
- Scene 1 (0-3s): <beat>
- Scene 2 (3-8s): <beat>
... one line per scene, with explicit start-end seconds ...

CTA (last 2-3s): <the call to action>

═══════════════════════════════════
IMAGE MANIFEST — <X> IMAGES
═══════════════════════════════════

For EACH image emit EXACTLY this block (header line starts with "IMAGE "):

IMAGE 1 - hook_bg.png
Scene: <which scene it appears in, with time range>
Content: <detailed description of what to draw>
Background: <solid hex colour, or "transparent">
Aspect ratio: <9:16 for full-screen backgrounds | 1:1 for characters, props and icons>
Resolution: <1080x1920 for backgrounds | 1080x1080 for characters>
PROMPT:
<the complete single-line AI image-generation prompt, style suffix already appended>

RULES FOR THE MANIFEST
- Filenames: lowercase snake_case, .png, descriptive, always unique.
- PROMPT: must be ONE single line (no line breaks inside a prompt).
- Always append this style suffix verbatim to every prompt: {STYLE_SUFFIX}
- Order the images in story order; backgrounds first where they matter.
- Count: 12-20 images for a 60s video, fewer for shorter videos. One clear purpose each.
- Every scene in the story arc needs at least one background or character image.
- Do NOT add commentary after the last block. Do NOT wrap the manifest in markdown fences.

Begin with the story plan, then the manifest."""

PHASE1_USER = """VIDEO BRIEF:
{brief}

TARGET DURATION: {duration} seconds
FORMAT: 1080x1920 (9:16 vertical)
{notes}

Run PHASE 1 now: story plan + the complete image manifest."""

CODE_SYSTEM_EXTRA = """

══════════════════════════════════════════════════════════════════════
  AUTOMATED PIPELINE CONTRACT (you are running inside a render server)
══════════════════════════════════════════════════════════════════════

- The ONLY images you may reference are the files listed in the ASSET MAP. Every
  `src` MUST be exactly `assets/<filename from the asset map>`.
- Never reference a filename that is not in the asset map. Never invent assets.
- A white punch flash div, the root stage ground and all layers must already be
  present in your file — you are shipping the FINAL composition, not a snippet.
- Output ONLY the complete file inside one ```html fence. No explanation after it.
- The server runs `hyperframes lint` on your file; it MUST pass with 0 errors.
"""

PHASE2_USER = """ASSETS ARE READY.

ASSET MAP — use these exact paths, nothing else:
{asset_map}

Total images available: {count}

Write the COMPLETE `index.html` for a {duration}-second 1080x1920 (9:16) 2D cartoon
video about: {brief}

Every image in the asset map MUST be used. Follow the composition contract, the GSAP
rules and the video style rules exactly. Output the full file in a ```html fence."""


def code_system_prompt() -> str:
    """Phase 2/3 system prompt — the repo's own agent instructions + contract."""
    base = REPO_ROOT / "SYSTEM_INSTRUCTIONS.md"
    text = ""
    if base.exists():
        text = base.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        text = (
            "You are the Nature AI Video Production Agent. Write a complete HyperFrames "
            "composition (index.html) for a 9:16 2D cartoon short."
        )
    return text.strip() + CODE_SYSTEM_EXTRA


LINT_FIX_USER = """`hyperframes lint` reported problems on your index.html:

{lint_output}

Fix EVERY reported issue. Rules that matter most:
- no CSS `transform` on any element GSAP animates (use xPercent/yPercent in the tween)
- never tween top/left/width/height/display/visibility/autoAlpha
- every asset src must exist in the ASSET MAP above
- exactly one paused GSAP timeline registered on window.__timelines
- build the timeline inside document.fonts.ready.then()

Output the COMPLETE corrected file again in a ```html fence. Nothing abbreviated."""