"""Nature AI Studio — web dashboard for the prompt → images → render pipeline.

Run with:  python -m uvicorn app:app --host 127.0.0.1 --port 8787   (from studio/)
The bundled ``run.ps1`` / ``run.sh`` launchers additionally open a Cloudflare
quick tunnel so the dashboard is reachable at a https://*.trycloudflare.com URL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import pipeline
import store
from config import STUDIO_DIR, settings

COOKIE_NAME = "nature_studio_session"
SESSION_TTL = 60 * 60 * 24 * 30
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    store.init()
    store.refresh_video_paths()
    pipeline.mark_interrupted()
    pipeline.reconcile()
    pipeline.bus.bind_loop(asyncio.get_running_loop())
    print(f"[studio] HyperFrames via '{' '.join(settings.hyperframes)}' | model {settings.model}", flush=True)
    if auth_enabled():
        print("[studio] Dashboard password protection: ON", flush=True)
    yield


app = FastAPI(
    title="Nature AI Studio",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,  # this dashboard is reachable from the public internet
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Auth (single shared password, HMAC-signed cookie)
# ─────────────────────────────────────────────────────────────────────────────

def _sign(payload: bytes) -> str:
    return hmac.new(settings.session_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _make_token() -> str:
    payload = json.dumps({"exp": int(time.time()) + SESSION_TTL}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") + "." + _sign(payload)


def _token_ok(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    body, _, sig = token.rpartition(".")
    try:
        padding = "=" * (-len(body) % 4)
        payload = base64.urlsafe_b64decode(body + padding)
        data = json.loads(payload)
    except (ValueError, TypeError):
        return False
    if not hmac.compare_digest(_sign(payload), sig):
        return False
    return int(data.get("exp", 0)) > time.time()


def auth_enabled() -> bool:
    return bool(settings.password)


def is_authed(request: Request) -> bool:
    if not auth_enabled():
        return True
    return _token_ok(request.cookies.get(COOKIE_NAME))


async def require_auth(request: Request) -> None:
    if not is_authed(request):
        raise HTTPException(status_code=401, detail="Password required")


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# ─────────────────────────────────────────────────────────────────────────────
#  Serialisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def project_payload(pid: str) -> dict:
    project = store.get_project(pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    slots = store.get_slots(pid)
    filled = sum(1 for s in slots if s["status"] == "ready")
    plan = {}
    if project.get("plan"):
        try:
            plan = json.loads(project["plan"])
        except ValueError:
            plan = {}
    return {
        # index_html is omitted: it is tens of KB and the dashboard fetches it
        # separately via /source only when asked.
        **{k: v for k, v in project.items() if k != "index_html"},
        "plan": plan,
        "index_html_len": len(project.get("index_html") or ""),
        "slots": [
            {
                "idx": s["idx"],
                "filename": s["filename"],
                "scene": s["scene"],
                "content": s["content"],
                "background": s["background"],
                "aspect": s["aspect"],
                "resolution": s["resolution"],
                "prompt": s["prompt"],
                "status": s["status"],
                "orig_name": s["orig_name"],
                "bytes": s["bytes"],
                "has_prompt": bool(s["prompt"]),
                "thumb": f"/api/projects/{pid}/slots/{s['idx']}/file" if s["status"] == "ready" else None,
            }
            for s in slots
        ],
        "filled": filled,
        "total": len(slots),
        "ready": bool(slots) and filled == len(slots),
        "running": pipeline.jobs.running(pid),
        "videos": [v for v in store.list_videos(500) if v["project_id"] == pid],
    }


def video_payload(video: dict) -> dict:
    return {
        **video,
        "file_url": f"/api/videos/{video['id']}/file",
        "download_url": f"/api/videos/{video['id']}/download",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Meta / auth routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/session")
async def session(request: Request):
    return {
        "auth_required": auth_enabled(),
        "authed": is_authed(request),
        "config": pipeline.public_api(),
        "server_time": time.time(),
    }


@app.post("/api/login")
async def login(payload: dict, request: Request):
    supplied = str(payload.get("password", ""))
    if not auth_enabled():
        return {"ok": True}
    if not hmac.compare_digest(supplied, settings.password):
        raise HTTPException(status_code=401, detail="Wrong password")
    forwarded = (request.headers.get("x-forwarded-proto") or "").lower()
    secure = forwarded == "https" or bool(os.getenv("STUDIO_SECURE_COOKIES", ""))
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        _make_token(),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return response


@app.get("/api/config", dependencies=[Depends(require_auth)])
async def config():
    return pipeline.public_api()


# ─────────────────────────────────────────────────────────────────────────────
#  Projects
# ─────────────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", (text or "").lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return (slug or "project")[:40]


@app.post("/api/projects", dependencies=[Depends(require_auth)])
async def create_project(payload: dict):
    brief = str(payload.get("brief", "")).strip()
    if not brief:
        raise HTTPException(status_code=400, detail="Describe the video you want first.")
    try:
        duration = max(10, min(180, int(payload.get("duration", 60))))
    except (TypeError, ValueError):
        duration = 60

    slug = slugify(brief)
    base, counter = slug, 2
    while (settings.projects_dir / slug).exists():
        slug = f"{base}-{counter}"
        counter += 1

    project = store.create_project(brief=brief, slug=slug, duration=duration, notes=str(payload.get("notes", ""))[:500])
    (settings.projects_dir / slug / "assets").mkdir(parents=True, exist_ok=True)
    (settings.projects_dir / slug / "prompts.txt").write_text(brief, encoding="utf-8")

    pipeline.jobs.start(project["id"], "analysis", lambda: pipeline.run_analysis(project["id"]))
    return project_payload(project["id"])


@app.get("/api/projects", dependencies=[Depends(require_auth)])
async def list_projects():
    return [project_payload(p["id"]) for p in store.list_projects(60)]


@app.get("/api/projects/{pid}", dependencies=[Depends(require_auth)])
async def get_project(pid: str):
    return project_payload(pid)


@app.delete("/api/projects/{pid}", dependencies=[Depends(require_auth)])
async def delete_project(pid: str):
    project = store.get_project(pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    target = (settings.projects_dir / project["slug"]).resolve()
    if target.is_dir() and str(target).startswith(str(settings.projects_dir.resolve())):
        shutil.rmtree(target, ignore_errors=True)
    store.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/analyze", dependencies=[Depends(require_auth)])
async def reanalyze(pid: str):
    """Re-run PHASE 1 (keeps nothing — fresh image manifest)."""
    project = store.get_project(pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if pipeline.jobs.running(pid):
        raise HTTPException(status_code=409, detail="A job is already running.")
    store.execute("DELETE FROM slots WHERE project_id = ?", (pid,))
    store.execute("DELETE FROM events WHERE project_id = ?", (pid,))
    store.update_project(pid, status="planning", error="", index_html="", image_count=0)
    pipeline.bus.publish(pid, "slots", {"slots": []})
    pipeline.jobs.start(pid, "analysis", lambda: pipeline.run_analysis(pid))
    return project_payload(pid)


@app.get("/api/projects/{pid}/source", dependencies=[Depends(require_auth)])
async def project_source(pid: str):
    project = store.get_project(pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"index_html": project.get("index_html", "")}


# ── Slots ───────────────────────────────────────────────────────────────────

@app.post("/api/projects/{pid}/slots/{idx}/image", dependencies=[Depends(require_auth)])
async def upload_slot(pid: str, idx: int, file: UploadFile = File(...)):
    project = store.get_project(pid)
    slot = store.get_slot(pid, idx)
    if not project or not slot:
        raise HTTPException(status_code=404, detail="Image slot not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        ext = mimetypes.guess_extension(file.content_type or "") or ".png"
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported image type ({ext}). Use PNG/JPG/WEBP.")

    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"Image is larger than {settings.max_upload_mb} MB.")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    target = pipeline.assets_dir(pid) / slot["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    store.mark_slot_filled(pid, idx, file.filename or slot["filename"], len(data))
    pipeline.log(pid, f"📎 {slot['filename']} ← {file.filename}", "ok")
    pipeline.bus.publish(pid, "slots", {"slots": store.get_slots(pid)})
    return project_payload(pid)


@app.delete("/api/projects/{pid}/slots/{idx}/image", dependencies=[Depends(require_auth)])
async def clear_slot(pid: str, idx: int):
    slot = store.get_slot(pid, idx)
    if not slot:
        raise HTTPException(status_code=404, detail="Image slot not found")
    target = pipeline.assets_dir(pid) / slot["filename"]
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass
    store.clear_slot(pid, idx)
    pipeline.bus.publish(pid, "slots", {"slots": store.get_slots(pid)})
    return project_payload(pid)


@app.get("/api/projects/{pid}/slots/{idx}/file")
async def slot_file(pid: str, idx: int, request: Request):
    if not is_authed(request):
        raise HTTPException(status_code=401, detail="Password required")
    slot = store.get_slot(pid, idx)
    if not slot:
        raise HTTPException(status_code=404, detail="Image slot not found")
    path = pipeline.assets_dir(pid) / slot["filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="No image uploaded yet")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ── Render trigger ──────────────────────────────────────────────────────────

@app.post("/api/projects/{pid}/render", dependencies=[Depends(require_auth)])
async def trigger_render(pid: str):
    project = store.get_project(pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if pipeline.jobs.running(pid):
        raise HTTPException(status_code=409, detail="A job is already running for this project.")
    if project["status"] == "rendering" and not store.get_slots(pid):
        raise HTTPException(status_code=409, detail="This project has no image manifest yet.")

    slots = store.get_slots(pid)
    if not slots:
        raise HTTPException(status_code=409, detail="Generate the image prompts first.")
    missing = [s["filename"] for s in slots if s["status"] != "ready"]
    if missing:
        raise HTTPException(status_code=409, detail=f"Add the missing image(s): {', '.join(missing[:6])}")

    pipeline.jobs.start(pid, "render", lambda: pipeline.run_render(pid))
    return project_payload(pid)


# ─────────────────────────────────────────────────────────────────────────────
#  Live progress (SSE)
# ─────────────────────────────────────────────────────────────────────────────

def _sse(event: dict) -> str:
    return (
        f"event: {event['type']}\n"
        f"id: {event.get('id', 0)}\n"
        f"data: {json.dumps(event['data'])}\n\n"
    )


@app.get("/api/projects/{pid}/events")
async def project_events(pid: str, request: Request, after: int = 0):
    if not is_authed(request):
        raise HTTPException(status_code=401, detail="Password required")
    if not store.get_project(pid):
        raise HTTPException(status_code=404, detail="Project not found")

    # Resume point: explicit query param wins, then the browser's Last-Event-Id.
    resume_from = after
    header = request.headers.get("last-event-id")
    if header and header.isdigit():
        resume_from = max(resume_from, int(header))

    queue = pipeline.bus.subscribe(pid)

    async def stream():
        # `last` is local to the generator on purpose: assigning it here must not
        # turn `resume_from` into an uninitialised closure local.
        last = resume_from
        try:
            backlog = store.get_events(pid, last)
            for event in backlog:
                last = event["id"]
                yield _sse(event)
            yield _sse({"id": last, "type": "ready", "data": {"ok": True}})

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=12)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                event_id = int(event.get("id", 0))
                if event_id <= last:
                    continue  # already delivered in the replay
                last = event_id
                yield _sse(event)
        except asyncio.CancelledError:
            raise
        finally:
            pipeline.bus.unsubscribe(pid, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
#  History + video delivery
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/history", dependencies=[Depends(require_auth)])
async def history():
    return [video_payload(v) for v in store.list_videos()]


@app.post("/api/history/import", dependencies=[Depends(require_auth)])
async def import_history():
    """Pick up MP4s already in renders/ that this studio did not create."""
    known = {str(Path(v["path"]).resolve()) for v in store.list_videos()}
    added = store.import_existing_renders(known, pipeline.probe_video)
    return {"added": added, "total": len(store.list_videos())}


@app.delete("/api/videos/{vid}", dependencies=[Depends(require_auth)])
async def delete_video(vid: str, keep_file: bool = True):
    video = store.get_video(vid)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if not keep_file:
        try:
            Path(video["path"]).unlink(missing_ok=True)
        except OSError:
            pass
    store.delete_video(vid)
    return {"ok": True}


def _range_response(path: Path, request: Request) -> Response:
    """Serve a file with byte-range support so <video> can seek."""
    size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    range_header = request.headers.get("range")
    start, end = 0, size - 1
    partial = False

    if range_header:
        match = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
        if match:
            raw_start, raw_end = match.group(1), match.group(2)
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else size - 1
            elif raw_end:  # suffix range
                start = max(0, size - int(raw_end))
            if start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            partial = True

    length = end - start + 1

    def iterator():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 512, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "public, max-age=86400",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(iterator(), status_code=206 if partial else 200, media_type=media_type, headers=headers)


def _find_video(vid: str, request: Request) -> dict:
    if not is_authed(request):
        raise HTTPException(status_code=401, detail="Password required")
    video = store.get_video(vid)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if not Path(video["path"]).exists():
        store.delete_video(vid)
        raise HTTPException(status_code=404, detail="File is no longer on disk")
    return video


@app.get("/api/videos/{vid}/file")
async def video_file(vid: str, request: Request):
    return _range_response(Path(_find_video(vid, request)["path"]), request)


@app.get("/api/videos/{vid}/download")
async def video_download(vid: str, request: Request):
    video = _find_video(vid, request)
    path = Path(video["path"])
    stem = path.stem
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{stem}.mp4",
        headers={"Content-Disposition": f'attachment; filename="{stem}.mp4"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Static dashboard
# ─────────────────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(STUDIO_DIR / "static")), name="static")


@app.middleware("http")
async def utf8_static(request: Request, call_next):
    """StaticFiles omits the charset, so spell it out for the emoji-bearing UI."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") and "charset=" not in response.headers.get("content-type", ""):
        media_type = response.headers.get("content-type", "text/plain").split(";")[0]
        response.headers["content-type"] = f"{media_type}; charset=utf-8"
    return response


@app.get("/")
async def index():
    return FileResponse(STUDIO_DIR / "static" / "index.html", media_type="text/html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)