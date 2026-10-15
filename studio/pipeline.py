"""The production pipeline: LLM phases + HyperFrames lint/render.

Jobs run on daemon threads (the OpenAI SDK and the CLI are blocking), events are
persisted to SQLite and fanned out to any number of SSE subscribers on the event
loop, so the dashboard shows live progress and survives a page reload.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

import store
from config import settings
from parsing import extract_html, parse_plan
from prompts import (
    PHASE1_SYSTEM,
    PHASE1_USER,
    PHASE2_USER,
    LINT_FIX_USER,
    code_system_prompt,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")

# Job statuses that mean "something is happening right now".
ACTIVE_STATUSES = {"planning", "generating_code", "linting", "rendering"}


# ─────────────────────────────────────────────────────────────────────────────
#  Event bus
# ─────────────────────────────────────────────────────────────────────────────

class EventBus:
    """Fan-out of pipeline events to SSE subscribers (thread-safe)."""

    def __init__(self) -> None:
        self._subs: dict[str, list] = {}
        self._lock = threading.Lock()
        self._loop = None

    def bind_loop(self, loop) -> None:
        self._loop = loop

    @staticmethod
    def _offer(queue, event: dict) -> None:
        """Push an event, evicting the oldest if a slow client fell behind."""
        import asyncio

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except Exception:  # noqa: BLE001 - a full queue is never fatal
                pass

    def publish(self, pid: str, type_: str, data: dict) -> dict:
        event = store.add_event(pid, type_, data)
        with self._lock:
            queues = list(self._subs.get(pid, []))
        loop = self._loop
        for queue in queues:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._offer, queue, event)
            else:  # no loop bound (unit tests / CLI smoke run)
                self._offer(queue, event)
        return event

    def subscribe(self, pid: str):
        import asyncio

        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs.setdefault(pid, []).append(queue)
        return queue

    def unsubscribe(self, pid: str, queue) -> None:
        with self._lock:
            queues = self._subs.get(pid)
            if not queues:
                return
            if queue in queues:
                queues.remove(queue)
            if not queues:
                self._subs.pop(pid, None)

    def listener_count(self, pid: str) -> int:
        with self._lock:
            return len(self._subs.get(pid, []))


bus = EventBus()


def log(pid: str, message: str, level: str = "info") -> None:
    text = str(message).strip()
    if text:
        bus.publish(pid, "log", {"text": text, "level": level})


def status(pid: str, value: str, detail: str = "", pct: int | None = None) -> None:
    store.update_project(pid, status=value, error="" if value != "failed" else detail)
    payload: dict = {"status": value, "detail": detail}
    if pct is not None:
        payload["progress"] = max(0, min(100, int(pct)))
    bus.publish(pid, "status", payload)


# ─────────────────────────────────────────────────────────────────────────────
#  LLM client
# ─────────────────────────────────────────────────────────────────────────────

class LLMError(RuntimeError):
    pass


@dataclass
class LLM:
    """Thin streaming wrapper over the OpenAI-compatible KiloCode gateway."""

    pid: str = ""
    system: str = ""

    def __post_init__(self) -> None:
        from openai import OpenAI

        if not settings.api_key:
            raise LLMError("KILOCODE_API_KEY is missing — add it to the repo-root .env file.")
        self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)

    def stream(self, messages: list[dict]) -> str:
        """Stream a completion into the project log; returns the full text."""
        # The system prompt changes per phase, so it is applied fresh every call
        # and never replayed from history.
        payload: list[dict] = [{"role": "system", "content": self.system}]
        for message in messages:
            role = message.get("role")
            if role == "system" or not message.get("content"):
                continue
            payload.append({"role": role, "content": message["content"]})
            store.add_message(self.pid, role, message["content"])

        last_error: Exception | None = None
        for attempt in range(1, settings.llm_max_attempts + 1):
            chunks: list[str] = []
            pending: list[str] = []
            last_flush = time.time()
            try:
                stream = self._client.chat.completions.create(
                    model=settings.model,
                    messages=payload,
                    max_tokens=settings.max_tokens,
                    temperature=settings.temperature,
                    stream=True,
                )
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    text = (getattr(delta, "content", None) or "") if delta else ""
                    if not text:
                        continue
                    chunks.append(text)
                    pending.append(text)
                    if time.time() - last_flush > 0.25:
                        bus.publish(self.pid, "llm", {"delta": "".join(pending), "final": False})
                        pending.clear()
                        last_flush = time.time()
                if pending:
                    bus.publish(self.pid, "llm", {"delta": "".join(pending), "final": False})
                full = "".join(chunks).strip()
                if full:
                    store.add_message(self.pid, "assistant", full)
                    bus.publish(self.pid, "llm", {"delta": "", "final": True, "chars": len(full)})
                return full
            except Exception as exc:  # noqa: BLE001 - upstream gateway is flaky
                last_error = exc
                if attempt < settings.llm_max_attempts:
                    # A free gateway answers 429 under load; that needs seconds,
                    # not milliseconds, so back off much harder for it.
                    rate_limited = "429" in str(exc) or "ratelimit" in type(exc).__name__.lower()
                    wait = attempt * (20 if rate_limited else 5)
                    try:
                        log(
                            self.pid,
                            f"LLM attempt {attempt}/{settings.llm_max_attempts} failed "
                            f"({type(exc).__name__}: {str(exc)[:160]}); retrying in {wait}s…",
                            "warn",
                        )
                    except Exception:  # noqa: BLE001 - never let logging mask the real error
                        pass
                    time.sleep(wait)
        raise LLMError(f"{type(last_error).__name__ if last_error else 'Error'}: {last_error}")


# ─────────────────────────────────────────────────────────────────────────────
#  Subprocess plumbing
# ─────────────────────────────────────────────────────────────────────────────

_SET_RE = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)="?([^"\r\n]*)"?', re.MULTILINE | re.IGNORECASE)
_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def _resolve_node_shim(exe: str) -> list[str] | None:
    """Resolve a Windows ``.cmd`` npm shim to ``[node, script.js]``.

    ``cmd`` re-splits quoted arguments on spaces, so ``npx lint "C:/my folder"``
    would arrive as two arguments. Invoking the JS entry point through ``node``
    sidesteps batch quoting entirely — which matters because project folders
    routinely contain spaces.
    """
    node = shutil.which("node")
    if not node:
        return None
    try:
        body = Path(exe).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # First assignment wins: npm shims set a concrete path first and then
    # conditionally override it with an indirection we would rather not follow.
    variables: dict[str, str] = {}
    for key, value in _SET_RE.findall(body):
        variables.setdefault(key.upper(), value)

    dp0 = str(Path(exe).resolve().parent)

    def expand(token: str) -> str:
        text = token
        for _ in range(3):
            before = text
            text = _VAR_RE.sub(lambda m: variables.get(m.group(1).upper(), m.group(0)), text)
            text = text.replace("%~dp0", dp0).replace("%dp0%", dp0)
            if text == before:
                break
        return text

    # The invocation line first ("%NODE_EXE%" "%NPX_CLI_JS%" %*), then any
    # other quoted path in the shim as a fallback.
    invocation = [line for line in reversed(body.splitlines()) if "%*" in line]
    tokens = [token for line in invocation for token in re.findall(r'"([^"]+)"', line)]
    tokens += re.findall(r'"([^"]+)"', body)

    for token in tokens:
        candidate = expand(token)
        if candidate.lower().endswith((".js", ".cjs", ".mjs")) and Path(candidate).exists():
            return [node, candidate]
    return None


def resolve_argv(command: str) -> list[str]:
    """Turn a command name into a directly executable argv prefix."""
    exe = shutil.which(command) or command
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return _resolve_node_shim(exe) or ["cmd", "/c", exe]
    return [exe]


def cli_argv(*args: str) -> list[str]:
    """Full argv for the HyperFrames CLI, handling Windows .cmd shims."""
    return [*resolve_argv(settings.hyperframes[0]), *settings.hyperframes[1:], *args]


def _child_env() -> dict:
    env = dict(os.environ)
    env.update({"FORCE_COLOR": "0", "NO_COLOR": "1", "CI": "1", "PYTHONIOENCODING": "utf-8"})
    env.pop("FORCE_HYPERFRAMES_TTY", None)
    return env


def _iter_output(proc: subprocess.Popen) -> Iterator[str]:
    """Yield output chunks split on CR and LF (progress bars use \\r)."""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    buffer = ""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except (OSError, ValueError):
            break
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")
        parts = re.split(r"[\r\n]+", buffer)
        buffer = parts.pop()
        for part in parts:
            cleaned = ANSI_RE.sub("", part).strip()
            if cleaned:
                yield cleaned
    cleaned = ANSI_RE.sub("", buffer).strip()
    if cleaned:
        yield cleaned


# ── progress interpretation ────────────────────────────────────────────────

# The CLI emits a machine-readable tick next to its progress bar:
#   ██████░░░░░░░░░░░░  37%  Capturing frame 33/120 (4 workers)
#   @hf-progress {"code":"capture","done":33,"total":120,"pct":37}
# `pct` is already the overall 0-100 render progress, so it is the source of
# truth; the bar line is only used for the human label.
_HF_PROGRESS_RE = re.compile(r"^@hf-progress\s+(\{.*\})\s*$")
_BAR_LINE_RE = re.compile(r"^[█▓▒░■●○◐]+\s")
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_COMPLETE_RE = re.compile(r"render complete|rendered in\s|video rendered", re.IGNORECASE)

_PHASE_LABELS = {
    "lint": "linting",
    "compile": "compiling composition",
    "bundle": "bundling",
    "resolve": "resolving assets",
    "extract": "extracting assets",
    "extract_video": "extracting video frames",
    "process_audio": "processing audio",
    "start_browsers": "starting browsers",
    "capture": "capturing frames",
    "encode": "encoding video",
    "assemble": "assembling video",
    "render": "rendering",
    "complete": "complete",
}

# Fallback spans, used only when a tick has done/total but no pct.
_SPAN = {
    "lint": (2, 9),
    "compile": (0, 6),
    "start_browsers": (6, 25),
    "capture": (25, 80),
    "encode": (80, 95),
    "assemble": (95, 100),
    "complete": (100, 100),
}


@dataclass
class ProgressTracker:
    """Maps raw HyperFrames output onto a single monotonic 0-100 progress."""

    phase: str = "starting"
    overall: int = 0
    label: str = ""
    structured: bool = False

    def feed(self, line: str) -> tuple[str, int] | None:
        stripped = line.strip()
        if not stripped:
            return None

        match = _HF_PROGRESS_RE.match(stripped)
        if match:
            try:
                data = json.loads(match.group(1))
            except ValueError:
                data = {}
            self.structured = True
            previous = (self.phase, self.overall)

            code = str(data.get("code") or "").lower()
            value = data.get("pct")
            if isinstance(value, (int, float)):
                self.overall = max(self.overall, int(value))
            elif isinstance(data.get("done"), int) and data.get("total"):
                base, end = _SPAN.get(code, (0, 100))
                fraction = min(1.0, max(0.0, data["done"] / max(1, data["total"])))
                self.overall = max(self.overall, int(base + fraction * (end - base)))

            label = _PHASE_LABELS.get(code)
            if label:
                self.phase = label
                self.label = label

            self.overall = max(0, min(100, self.overall))
            return None if (self.phase, self.overall) == previous else (self.phase, self.overall)

        if _BAR_LINE_RE.match(stripped):
            self.label = _BAR_LINE_RE.sub("", stripped).strip()
            return None

        if _COMPLETE_RE.search(stripped):
            if self.overall < 100:
                self.phase, self.overall = "complete", 100
                return self.phase, self.overall
            return None

        # Lint runs before any render ticks, so give it a percentage of its own.
        if not self.structured and "lint" in stripped.lower() and _PERCENT_RE.search(stripped):
            self.overall = max(self.overall, 5)
            self.phase = "linting"
            return self.phase, self.overall

        return None


def _publish_progress(pid: str, tracker: ProgressTracker, line: str) -> None:
    result = tracker.feed(line)
    if result:
        phase, overall = result
        bus.publish(
            pid,
            "progress",
            {"phase": phase, "progress": overall, "line": (tracker.label or line)[:200]},
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Job runner
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Job:
    kind: str
    thread: threading.Thread
    started: float = field(default_factory=time.time)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def running(self, pid: str) -> bool:
        with self._lock:
            job = self._jobs.get(pid)
        return bool(job and job.thread.is_alive())

    def start(self, pid: str, kind: str, fn: Callable[[], None]) -> None:
        with self._lock:
            existing = self._jobs.get(pid)
            if existing and existing.thread.is_alive():
                raise RuntimeError(f"A {existing.kind} job is already running for this project.")

            def runner() -> None:
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
                    status(pid, "failed", str(exc))
                    log(pid, f"✗ Failed: {exc}", "error")
                finally:
                    store.trim_events(pid)
                    with self._lock:
                        self._jobs.pop(pid, None)

            thread = threading.Thread(target=runner, name=f"job-{pid}-{kind}", daemon=True)
            self._jobs[pid] = _Job(kind=kind, thread=thread)
        thread.start()


jobs = JobManager()


# ─────────────────────────────────────────────────────────────────────────────
#  Project paths
# ─────────────────────────────────────────────────────────────────────────────

def project_dir(pid: str) -> Path:
    project = store.get_project(pid)
    if not project:
        raise FileNotFoundError(pid)
    return settings.projects_dir / project["slug"]


def assets_dir(pid: str) -> Path:
    path = project_dir(pid) / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slot_path(pid: str, slot: dict) -> Path:
    return assets_dir(pid) / slot["filename"]


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 1 — brief → story plan + image manifest
# ─────────────────────────────────────────────────────────────────────────────

def run_analysis(pid: str) -> None:
    project = store.get_project(pid)
    if not project:
        return

    status(pid, "planning", "Analysing brief…", 2)
    log(pid, f"PHASE 1 — analysing brief with {settings.model}…")

    llm = LLM(pid=pid, system=PHASE1_SYSTEM)
    notes = f"\nEXTRA DIRECTION: {project['notes']}" if project.get("notes") else ""
    raw = llm.stream(
        [
            {
                "role": "user",
                "content": PHASE1_USER.format(
                    brief=project["brief"],
                    duration=project["duration"],
                    notes=notes,
                ),
            }
        ]
    )

    if not raw.strip():
        raise LLMError("The model returned an empty plan.")

    plan, slots = parse_plan(raw, min_slots=1)
    store.replace_slots(pid, slots)
    store.update_project(
        pid,
        analysis=plan["text"],
        plan=json.dumps({k: v for k, v in plan.items() if k != "text"}, ensure_ascii=False),
        image_count=len(slots),
    )

    filled = sum(1 for s in slots if s.get("prompt"))
    log(pid, f"✓ Plan ready — {len(slots)} image prompts ({filled} with full prompt text).", "ok")
    for slot in slots:
        log(pid, f"  • {slot['filename']}  —  {slot.get('scene') or slot.get('content', '')[:60]}")

    bus.publish(pid, "slots", {"slots": store.get_slots(pid)})
    status(pid, "awaiting_images", f"Upload {len(slots)} images to continue.", 100)


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 2/3 — code generation → lint → render
# ─────────────────────────────────────────────────────────────────────────────

def _asset_map(pid: str) -> str:
    slots = store.get_slots(pid)
    lines = []
    for slot in slots:
        path = assets_dir(pid) / slot["filename"]
        exists = "✓" if path.exists() else "✗"
        detail = slot.get("scene") or slot.get("content", "")
        lines.append(f"assets/{slot['filename']}  [{exists}]  <- {slot['filename']}  |  {detail[:90]}")
    return "\n".join(lines)


def _conversation(pid: str, max_chars: int = 46000) -> list[dict]:
    """Recent non-system turns, oldest trimmed first so the newest stay intact."""
    turns = [m for m in store.get_messages(pid) if m["role"] != "system"]
    total = sum(len(m["content"]) for m in turns)
    while total > max_chars and len(turns) > 1:
        total -= len(turns.pop(0)["content"])
    return turns


def _write_composition(pid: str, html: str) -> Path:
    path = project_dir(pid) / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    store.update_project(pid, index_html=html)
    return path


def _is_noise(line: str) -> bool:
    """CLI-internal chatter that is useful in the buffer but not in the UI."""
    return bool(_NOISE_RE.match(line.strip()))


_NOISE_RE = re.compile(r"^(\[INFO\]|\[DEBUG\]|\[VERBOSE\]|\[BrowserManager\]|\[initSession|\[Calibration)")


def _run_cli(pid: str, args: list[str], *, timeout: int | None = None) -> tuple[int, list[str]]:
    """Run the HyperFrames CLI, streaming every line to the log + progress bar."""
    argv = cli_argv(*args)
    log(pid, "$ hyperframes " + " ".join(args), "cmd")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        argv,
        cwd=str(settings.projects_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_child_env(),
        creationflags=creationflags,
    )
    lines: list[str] = []
    tracker = ProgressTracker()
    deadline = time.time() + (timeout or settings.render_timeout_s)
    for line in _iter_output(proc):
        lines.append(line)
        if not _is_noise(line):
            log(pid, line)
        _publish_progress(pid, tracker, line)
        if len(lines) > 4000:
            lines = lines[-2000:]
        if timeout and time.time() > deadline:
            proc.kill()
            raise TimeoutError(f"hyperframes {args[0]} timed out after {timeout}s")
    code = proc.wait()
    return code, lines


_LINT_COUNT_RE = re.compile(r"(\d+)\s*(?:error|errors|warning|warnings)", re.IGNORECASE)


def _lint_error_count(lines: list[str]) -> int:
    for line in reversed(lines):
        match = _LINT_COUNT_RE.search(line)
        if match:
            return int(match.group(1))
    return -1


def run_render(pid: str) -> None:
    project = store.get_project(pid)
    if not project:
        return

    slots = store.get_slots(pid)
    missing = [s for s in slots if not (assets_dir(pid) / s["filename"]).exists()]
    if missing:
        raise RuntimeError(
            f"{len(missing)} image(s) still missing: " + ", ".join(s["filename"] for s in missing[:5])
        )

    directory = project_dir(pid)
    directory.mkdir(parents=True, exist_ok=True)
    settings.renders_dir.mkdir(parents=True, exist_ok=True)

    history = _conversation(pid)
    llm = LLM(pid=pid, system=code_system_prompt())

    # ── Generate the composition ────────────────────────────────────────────
    status(pid, "generating_code", "Writing the HyperFrames composition…", 5)
    log(pid, "PHASE 2/3 — generating animation code…")
    response = llm.stream(
        list(history)
        + [
            {
                "role": "user",
                "content": PHASE2_USER.format(
                    asset_map=_asset_map(pid),
                    count=len(slots),
                    duration=project["duration"],
                    brief=project["brief"],
                ),
            }
        ]
    )

    html = extract_html(response)
    if not html:
        path = directory / "output.txt"
        path.write_text(response, encoding="utf-8")
        raise LLMError("Could not extract index.html from the model output (saved to output.txt).")

    html_path = _write_composition(pid, html)
    log(pid, f"✓ Wrote {html_path.name} ({len(html):,} chars)", "ok")

    # ── Lint, with an auto-fix loop ─────────────────────────────────────────
    for attempt in range(1, settings.lint_attempts + 1):
        status(pid, "linting", f"Linting (attempt {attempt}/{settings.lint_attempts})…", 12)
        code, lines = _run_cli(pid, ["lint", str(directory)], timeout=300)
        errors = _lint_error_count(lines)
        if code == 0 or errors == 0:
            log(pid, "✓ Lint passed with 0 errors.", "ok")
            break
        if attempt == settings.lint_attempts:
            log(pid, f"⚠ Lint still reports {errors} issue(s) — rendering anyway.", "warn")
            break
        log(pid, f"⚠ Lint reported {errors} issue(s) — asking the model to fix them…", "warn")
        fixed = llm.stream(
            _conversation(pid)
            + [
                {
                    "role": "user",
                    "content": LINT_FIX_USER.format(
                        lint_output="\n".join(lines[-80:]) or "lint failed without output"
                    ),
                }
            ]
        )
        fixed_html = extract_html(fixed)
        if not fixed_html:
            log(pid, "⚠ Fix pass produced no HTML — keeping the previous composition.", "warn")
            break
        html_path = _write_composition(pid, fixed_html)
        log(pid, f"✓ Rewrote {html_path.name} ({len(fixed_html):,} chars)", "ok")

    # ── Render ──────────────────────────────────────────────────────────────
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_name = f"{project['slug']}_{stamp}.mp4"
    out_path = settings.renders_dir / out_name

    status(pid, "rendering", "Rendering video…", 12)
    log(pid, "PHASE 3 — rendering. This can take a few minutes.", "info")
    tracker = ProgressTracker()
    code, lines = _run_cli(pid, ["render", str(directory), "--output", str(out_path)])

    if code != 0 or not out_path.exists():
        candidates = sorted(settings.renders_dir.glob(f"{project['slug']}_*.mp4"), reverse=True)
        if not candidates:
            tail = "\n".join(lines[-15:])
            raise RuntimeError(f"Render failed (exit {code}).\n{tail}")
        out_path = candidates[0]
        out_name = out_path.name

    width, height, duration = probe_video(out_path)
    vid = store.add_video(
        project_id=pid,
        filename=out_name,
        path=str(out_path),
        bytes=out_path.stat().st_size,
        duration=duration,
        width=width,
        height=height,
        brief=project["brief"],
        slug=project["slug"],
    )

    log(pid, f"✓ Render complete — {out_name}", "ok")
    bus.publish(pid, "video", {"video": store.get_video(vid)})
    status(pid, "done", "Video ready in History.", 100)


# ─────────────────────────────────────────────────────────────────────────────
#  ffprobe
# ─────────────────────────────────────────────────────────────────────────────

def probe_video(path: Path) -> tuple[int, int, float]:
    argv = [
        *resolve_argv("ffprobe"),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        out = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=creationflags,
        ).stdout
        data = json.loads(out or "{}")
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0, 0, 0.0

    duration = 0.0
    fmt = data.get("format") or {}
    if fmt.get("duration"):
        try:
            duration = round(float(fmt["duration"]), 2)
        except ValueError:
            duration = 0.0
    width = height = 0
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if not duration and stream.get("duration"):
                try:
                    duration = round(float(stream["duration"]), 2)
                except ValueError:
                    pass
            break
    return width, height, duration


# ─────────────────────────────────────────────────────────────────────────────
#  Bootstrap helpers
# ─────────────────────────────────────────────────────────────────────────────

def mark_interrupted() -> None:
    """Any project left mid-flight from a previous process run is stale."""
    for project in store.list_projects(500):
        if project["status"] in ACTIVE_STATUSES:
            store.update_project(project["id"], status="interrupted")


def reconcile() -> None:
    """Drop rows whose project folder and composition are both gone."""
    for project in store.list_projects(500):
        folder = settings.projects_dir / project["slug"]
        if folder.exists():
            continue
        if project.get("index_html"):
            store.update_project(project["id"], status="interrupted")
        elif not [v for v in store.list_videos(500) if v["project_id"] == project["id"]]:
            store.delete_project(project["id"])


def public_api() -> dict:
    return {
        "model": settings.model,
        "hyperframes": " ".join(settings.hyperframes),
        "projects_dir": str(settings.projects_dir),
        "renders_dir": str(settings.renders_dir),
        "max_upload_mb": settings.max_upload_mb,
        "public_url": settings.public_url(),
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    store.init()
    print("pipeline module OK —", public_api(), file=sys.stderr)