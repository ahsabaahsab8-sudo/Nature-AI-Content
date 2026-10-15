"""SSE endpoint check: replay of stored events, live delivery, and Last-Event-Id.

    python test_sse.py [base_url]
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.cookiejar import CookieJar

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def login() -> None:
    """Authenticate when the dashboard has a password configured."""
    from config import settings

    if not settings.password:
        return
    payload = json.dumps({"password": settings.password}).encode()
    req = urllib.request.Request(
        BASE + "/api/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    OPENER.open(req, timeout=30).read()
    print(f"   logged in (password from .env, {len(settings.password)} chars)")


def get(path: str, timeout: float = 30) -> dict:
    with OPENER.open(BASE + path, timeout=timeout) as res:
        return json.loads(res.read().decode())


def open_stream(path: str, headers: dict | None = None):
    return OPENER.open(urllib.request.Request(BASE + path, headers=headers or {}), timeout=30)


def request(method: str, path: str, data: bytes | None = None, content_type: str | None = None):
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": content_type} if content_type else {},
    )
    with OPENER.open(req, timeout=60) as res:
        body = res.read()
    return json.loads(body) if body else {}


def parse_frames(lines) -> list[tuple[str, int]]:
    """Group SSE lines into (event, id) pairs, one per blank-line-terminated frame."""
    frames: list[tuple[str, int]] = []
    name, ident = "", 0
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("id: "):
            ident = int(line[4:])
        elif line == "":
            if name:
                frames.append((name, ident))
            name, ident = "", 0
    return frames


def main() -> None:
    login()
    projects = get("/api/projects")
    if not projects:
        raise SystemExit("No project to test against — run the studio and create one first.")
    pid = projects[0]["id"]

    print("1. replay + ready frame")
    raw_lines: list[bytes] = []
    stream = open_stream(f"/api/projects/{pid}/events?after=0")

    def reader() -> None:
        try:
            for raw in stream:
                raw_lines.append(raw)
        except Exception:  # noqa: BLE001 - the socket closes under us on teardown
            pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    time.sleep(3)
    stream.close()
    thread.join(timeout=5)
    events = parse_frames(raw_lines)

    if not events:
        raise SystemExit("FAIL: no SSE frames received")
    print(f"   {len(events)} frames, last = {events[-1][0]} id={events[-1][1]}")
    assert events[0][0] in {"log", "status", "llm"}, f"unexpected first frame: {events[0]}"
    assert any(name == "ready" for name, _ in events), "missing 'ready' frame"
    assert [i for _, i in events] == sorted(i for _, i in events), "frames out of order"

    print("2. resume from Last-Event-Id skips replayed frames")
    last_id = max(i for _, i in events)
    resumed_lines: list[bytes] = []
    stream = open_stream(f"/api/projects/{pid}/events", {"Last-Event-Id": str(last_id)})
    for raw in stream:
        resumed_lines.append(raw)
        if len(resumed_lines) > 4:
            break
    stream.close()
    resumed = [i for _, i in parse_frames(resumed_lines)]
    print(f"   resumed frames: {resumed}")
    assert resumed[0] == last_id, f"resume point ignored (first id {resumed[0]} != {last_id})"

    print("3. live delivery of a server-side event")

    from config import settings
    import store

    store.init()
    project = store.get_project(pid)
    slot = store.get_slots(pid)[0]
    image = settings.projects_dir / project["slug"] / "assets" / slot["filename"]
    backup = image.read_bytes()
    print(f"   using slot {slot['idx']} ({slot['filename']}, {len(backup):,} bytes)")

    received: list[str] = []
    stream = open_stream(f"/api/projects/{pid}/events?after={last_id}")

    def live_reader() -> None:
        try:
            for raw in stream:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event: "):
                    received.append(line[7:])
                if "slots" in received:
                    break
        except Exception:  # noqa: BLE001 - the socket closes under us on teardown
            pass

    thread = threading.Thread(target=live_reader, daemon=True)
    thread.start()
    time.sleep(1)

    request("DELETE", f"/api/projects/{pid}/slots/{slot['idx']}/image")
    thread.join(timeout=10)
    stream.close()
    print(f"   live frames: {received}")
    assert "slots" in received, "live event never arrived"

    # restore the slot so the project stays renderable
    boundary = "----studio-sse"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{slot["filename"]}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + backup + f"\r\n--{boundary}--\r\n".encode()
    request(
        "POST",
        f"/api/projects/{pid}/slots/{slot['idx']}/image",
        data=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    print("PASS")


if __name__ == "__main__":
    main()