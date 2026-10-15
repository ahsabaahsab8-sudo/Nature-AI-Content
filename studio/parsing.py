"""Turns the PHASE 1 LLM answer into a story plan + a list of image slots.

The model is asked for a rigid block format, but LLM output drifts — so the
parser is deliberately forgiving: any header variant works, missing fields fall
back to sensible defaults, and a completely unstructured answer still yields one
usable slot instead of crashing the pipeline.
"""

from __future__ import annotations

import re

IMAGE_EXTS = ("png", "jpg", "jpeg", "webp")

_HEADER_RE = re.compile(
    r"^[ \t]{0,8}(?:#{1,6}[ \t]*)?[\*>_~\-]{0,3}[ \t]*"
    r"IMAGE[ \t]*#?[ \t]*(\d{1,3})"
    r"[ \t]*(?:[-–—:>\|]{1,3}|[ \t])[ \t]*"
    r"[\`\"'\*_]*([^`\"'\n]*?)[\`\"'\*_]*[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]*\.(?:png|jpe?g|webp)", re.IGNORECASE)

_LABEL_RE = re.compile(
    r"^[ \t]*[*_#>\-]*[ \t]*(scene|content|background|aspect\s*ratio|aspect|resolution|size|prompt)"
    r"[ \t]*[*_#]*[ \t]*:[ \t]*",
    re.IGNORECASE,
)

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^[\*_>]{1,3}\s*", "", line)
    line = re.sub(r"[\*_`]{1,3}$", "", line)
    line = line.replace("�?", "-").replace("—", "-").replace("–", "-")
    return line.strip()


def slugify_filename(raw: str, idx: int) -> str:
    """Coerce whatever the model wrote into a safe, lowercase image filename."""
    match = _FILENAME_RE.search(raw or "")
    candidate = match.group(0) if match else (raw or "")
    stem, _, ext = candidate.rpartition(".")
    ext = ext.lower()
    if ext not in IMAGE_EXTS:
        ext, stem = "", candidate
    base = _SLUG_STRIP_RE.sub("_", stem.lower()).strip("_")
    base = re.sub(r"_{2,}", "_", base)
    if not base:
        base = f"image_{idx:02d}"
    if len(base) > 56:
        base = base[:56].rstrip("_")
    return f"{base}.{ext or 'png'}"


def unique_filename(name: str, taken: set[str], idx: int) -> str:
    if name not in taken:
        taken.add(name)
        return name
    stem, _, ext = name.rpartition(".")
    stem = stem or f"image_{idx:02d}"
    for n in range(2, 99):
        candidate = f"{stem}_{n}.{ext}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    candidate = f"image_{idx:02d}_{taken.__len__()}.png"
    taken.add(candidate)
    return candidate


def _parse_block(idx: int, raw_header_tail: str, body: str, taken: set[str]) -> dict:
    fields: dict[str, str] = {}
    prompt_lines: list[str] = []
    current: str | None = None

    for raw_line in body.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        label = _LABEL_RE.match(line)
        if label:
            key = label.group(1).lower().replace(" ", "")
            value = line[label.end():].strip()
            key = {
                "aspect": "aspect",
                "aspectratio": "aspect",
                "size": "resolution",
            }.get(key, key)
            current = key
            fields[key] = value
            if key == "prompt":
                if value:
                    prompt_lines.append(value)
            continue

        if current == "prompt":
            prompt_lines.append(line)
        elif current in ("content", "scene"):
            fields[current] = (fields.get(current, "") + " " + line).strip()
        elif current is None:
            # stray text before any label — keep as a description fallback
            fields.setdefault("content", line)

    prompt = " ".join(p for p in prompt_lines if p).strip()
    filename = unique_filename(slugify_filename(raw_header_tail, idx), taken, idx)

    return {
        "idx": idx,
        "filename": filename,
        "scene": fields.get("scene", ""),
        "content": fields.get("content", ""),
        "background": fields.get("background", ""),
        "aspect": fields.get("aspect", ""),
        "resolution": fields.get("resolution", ""),
        "prompt": prompt or fields.get("content", ""),
    }


def extract_scenes(analysis: str) -> list[str]:
    scenes: list[str] = []
    for line in analysis.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*\u2022]\s*\**\s*scene\s*\d+", stripped, re.IGNORECASE):
            scenes.append(_clean_line(stripped).lstrip("-*\u2022 "))
    return scenes


def extract_field(analysis: str, label: str) -> str:
    """Grab 'Label (anything): value' from the free-form analysis block."""
    pattern = re.compile(
        rf"^[ \t]*[*_#>\-]*{label}\b[^\n:]*:[ \t]*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(analysis)
    return _clean_line(match.group(1)).strip() if match else ""


def parse_plan(raw: str, min_slots: int = 1) -> tuple[dict, list[dict]]:
    """Return ``(analysis, slots)`` for a PHASE 1 LLM answer."""
    text = (raw or "").replace("\r\n", "\n").strip()
    headers = list(_HEADER_RE.finditer(text))

    slots: list[dict] = []
    taken: set[str] = set()

    if headers:
        analysis = text[: headers[0].start()].strip()
        for i, match in enumerate(headers):
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            body = text[match.end() : end]
            slots.append(_parse_block(i + 1, match.group(2), body, taken))
    else:
        analysis = text
        if text:
            first = text.splitlines()[0]
            slug = slugify_filename(first, 1)
            slots.append(
                {
                    "idx": 1,
                    "filename": slug,
                    "scene": "",
                    "content": text[:900],
                    "background": "",
                    "aspect": "",
                    "resolution": "",
                    "prompt": text,
                }
            )

    # Renumber defensively and guarantee the slot count the UI promised.
    for i, slot in enumerate(slots, start=1):
        slot["idx"] = i
    while len(slots) < min_slots:
        idx = len(slots) + 1
        slots.append(
            {
                "idx": idx,
                "filename": unique_filename(f"image_{idx:02d}.png", taken, idx),
                "scene": "",
                "content": "",
                "background": "",
                "aspect": "",
                "resolution": "",
                "prompt": "",
            }
        )

    if not analysis.strip():
        analysis = f"{len(slots)} image(s) generated for the brief."

    plan = {
        "hook": extract_field(analysis, "Hook"),
        "tone": extract_field(analysis, "Tone"),
        "cta": extract_field(analysis, "CTA"),
        "duration": extract_field(analysis, "Total Duration"),
        "scenes": extract_scenes(analysis),
        "text": analysis,
    }
    return plan, slots


def extract_html(response: str) -> str:
    """Pull the composition HTML out of an LLM reply."""
    text = response or ""
    fence = re.search(r"```html\s*(<!DOCTYPE.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    fence = re.search(r"```\s*(<!DOCTYPE.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    start = text.lower().find("<!doctype html")
    if start == -1:
        start = text.lower().find("<html")
    end = text.lower().rfind("</html>")
    if start != -1 and end != -1 and end > start:
        return text[start : end + len("</html>")].strip()
    return ""