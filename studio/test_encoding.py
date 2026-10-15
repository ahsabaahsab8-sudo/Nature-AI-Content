"""Check the studio static assets for mojibake (a cp1252 round-trip through PowerShell)."""

from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"

SUSPECT = set("ÃÂðÐÑ\u201a\u2019\u201e\u2020\u2022\u2030\u0152\u0178")


def scan(path: Path) -> list[tuple[int, str]]:
    hits = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        odd = sorted({ch for ch in line if ord(ch) > 127})
        if odd and (SUSPECT & set(odd)):
            hits.append((number, "".join(odd)))
    return hits


def main() -> None:
    bad = False
    for path in sorted(STATIC.glob("*")):
        if path.is_dir():
            continue
        hits = scan(path)
        if hits:
            bad = True
            print(f"{path.name}: mojibake on lines {[n for n, _ in hits]}")
            for number, chars in hits[:5]:
                print(f"   {number}: {chars}")
        else:
            print(f"{path.name}: clean")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()