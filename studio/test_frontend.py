"""Static consistency check: every $(id) used in app.js must exist in index.html."""

import re
from pathlib import Path

here = Path(__file__).resolve().parent
html = (here / "static" / "index.html").read_text(encoding="utf-8")
js = (here / "static" / "app.js").read_text(encoding="utf-8")

ids = set(re.findall(r'id="([^"]+)"', html))
used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", js))
dynamic = set(re.findall(r"\$\(`([A-Za-z0-9_-]+)", js))

missing = sorted(used - ids)
print("ids in html :", len(ids))
print("ids used    :", len(used))
print("missing     :", missing or "none")
print("dynamic     :", sorted(dynamic))

css_classes = set(re.findall(r"class=\"([^\"]+)\"", html))
unused_css = []
for block in re.findall(r"\.([a-zA-Z][\w-]*)\s*[,{]", (here / "static" / "style.css").read_text(encoding="utf-8")):
    if block not in css_classes and block not in js and block not in html:
        unused_css.append(block)
print("css classes never referenced:", unused_css or "none")

raise SystemExit(1 if missing else 0)