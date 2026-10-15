"""End-to-end smoke test for the studio API (dev helper, not part of the app).

    python smoke_test.py [base_url]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
ASSETS = Path(__file__).resolve().parent.parent / "create-a-video-2d-animation-of-1-minute-" / "assets"
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def login() -> None:
    from config import settings

    if not settings.password:
        return
    call("POST", "/api/login", {"password": settings.password})
    print(f"   logged in (password from .env, {len(settings.password)} chars)")


def call(method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with OPENER.open(req, timeout=60) as res:
        body = res.read().decode()
    return json.loads(body) if body else {}


def upload(pid: str, idx: int, file: Path) -> dict:
    boundary = "----studio" + str(int(time.time() * 1000))
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file.name}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    body = head + file.read_bytes() + tail
    req = urllib.request.Request(
        f"{BASE}/api/projects/{pid}/slots/{idx}/image",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with OPENER.open(req, timeout=180) as res:
        return json.loads(res.read().decode())


def wait_for_status(pid: str, wanted: set[str], timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        project = call("GET", f"/api/projects/{pid}")
        if project["status"] != last:
            print(f"    status: {project['status']}")
            last = project["status"]
        if project["status"] in wanted:
            return project
        time.sleep(3)
    raise TimeoutError(f"status stuck at {last}")


def main() -> None:
    print("1. session")
    print("   ", json.dumps(call("GET", "/api/session")["config"])[:160])
    login()

    print("2. create project (runs PHASE 1 LLM)")
    project = call(
        "POST",
        "/api/projects",
        {"brief": "3 poisonous mushrooms that can kill you - fear hook, Nature AI app reveal", "duration": 30},
    )
    pid = project["id"]
    print("    ", pid, project["slug"])

    project = wait_for_status(pid, {"awaiting_images", "failed"}, timeout=600)
    if project["status"] == "failed":
        raise SystemExit("PHASE 1 failed — see the console tab")

    print(f"3. plan: {len(project['slots'])} image slots")
    for slot in project["slots"][:4]:
        print(f"     {slot['idx']:>2}. {slot['filename']:<28} {slot['scene'][:30]:<30} prompt={len(slot['prompt'])}")

    files = sorted(ASSETS.glob("*.jpg"))
    if not files:
        raise SystemExit(f"No test images found in {ASSETS}")
    print(f"4. uploading {len(project['slots'])} images (cycling {len(files)} real assets)")
    for i, slot in enumerate(project["slots"]):
        upload(pid, slot["idx"], files[i % len(files)])
        print(f"     ✓ {slot['idx']}/{len(project['slots'])} {slot['filename']}")

    project = call("GET", f"/api/projects/{pid}")
    print(f"    ready={project['ready']} filled={project['filled']}/{project['total']}")

    print("5. triggering render (PHASE 2/3: code -> lint -> render)")
    call("POST", f"/api/projects/{pid}/render")
    project = wait_for_status(pid, {"done", "failed"}, timeout=2400)

    if project["status"] == "failed":
        raise SystemExit("render failed — check the console tab")

    video = project["videos"][-1]
    print("6. video:", json.dumps({k: video[k] for k in ("filename", "bytes", "duration", "width", "height")}))

    req = urllib.request.Request(f"{BASE}/api/videos/{video['id']}/file", headers={"Range": "bytes=0-1023"})
    with OPENER.open(req, timeout=60) as res:
        head = res.read()
        print("   range request:", res.status, res.headers.get("Content-Range"), len(head), "bytes")

    print("7. history:", len(call("GET", "/api/history")), "video(s)")
    print("PASS")


if __name__ == "__main__":
    main()