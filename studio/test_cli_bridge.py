"""Validate the CLI bridge: arg quoting, output streaming, progress parsing.

    python test_cli_bridge.py
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pipeline
import store

COMPOSITION = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>studio bridge probe</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #000; overflow: hidden; }
  #root { width: 1080px; height: 1920px; position: relative; overflow: hidden; background: #0d1b12; }
  .clip { position: absolute; }
</style>
</head>
<body>
<div id="root" data-composition-id="probe" data-width="1080" data-height="1920" data-duration="1" data-fps="30">
  <div class="clip" id="sun" data-start="0" data-duration="1"
       style="left:390px;top:700px;width:300px;height:300px;border-radius:50%;background:#ffd857;"></div>
  <div class="clip" id="label" data-start="0" data-duration="1"
       style="left:240px;top:1050px;color:#ffffff;font:900 120px/1.2 sans-serif;text-align:center;width:600px;">BRIDGE</div>
</div>
<script>
document.fonts.ready.then(() => {
  const tl = gsap.timeline({ paused: true });
  tl.fromTo('#sun', { scale: 0, opacity: 0 }, { scale: 1, opacity: 1, duration: 0.6, ease: 'back.out(2)' }, 0);
  tl.fromTo('#label', { y: 120, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: 'back.out(1.8)' }, 0.3);
  window.__timelines = window.__timelines || {};
  window.__timelines['probe'] = tl;
});
</script>
</body>
</html>
"""


def main() -> None:
    store.init()
    pid = "_probe"
    root = Path(__file__).resolve().parent.parent
    project = root / "studio bridge probe"
    shutil.rmtree(project, ignore_errors=True)
    (project / "assets").mkdir(parents=True)
    (project / "index.html").write_text(COMPOSITION, encoding="utf-8")

    print("1. lint (folder name contains spaces)")
    code, lines = pipeline._run_cli(pid, ["lint", str(project)], timeout=300)
    print(f"   exit={code}")
    for line in lines[-6:]:
        print("   |", line)
    assert code == 0, "lint failed"

    out = root / "renders" / "studio bridge probe.mp4"
    out.unlink(missing_ok=True)

    print("2. render with progress parsing")
    tracker = pipeline.ProgressTracker()
    samples: list[tuple[str, int]] = []
    code, lines = pipeline._run_cli(pid, ["render", str(project), "--output", str(out)])
    print(f"   exit={code}")
    for line in lines:
        result = tracker.feed(line)
        if result and result[0] != (samples[-1][0] if samples else None):
            samples.append(result)
    for line in lines[-8:]:
        print("   |", line)
    print("   phase timeline:", samples)
    assert out.exists(), "no output file"
    assert tracker.overall >= 90, f"progress only reached {tracker.overall}"

    width, height, duration = pipeline.probe_video(out)
    print(f"3. probe: {width}x{height} {duration}s {out.stat().st_size:,} bytes")
    assert width == 1080 and height == 1920

    out.unlink(missing_ok=True)
    shutil.rmtree(project, ignore_errors=True)
    store.execute("DELETE FROM events WHERE project_id = ?", (pid,))
    print("PASS")


if __name__ == "__main__":
    main()