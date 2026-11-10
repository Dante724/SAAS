#!/usr/bin/env python3
"""Fails while any page the site serves still has a [[CAPITALISED]] legal placeholder.

Run from the repo root:  python3 website/tools/check_legal_placeholders.py [--warn-only] [site_root]

build_pages.py refuses to build with placeholders left, but the built pages are committed, so
deploying website/ as it is would skip that check. CI runs this on the committed tree with
--warn-only until the founder's legal details are in.
The sources in tools/legal/ keep the tokens until the founder fills them in, so tools/ and the
README are not scanned.
"""
import sys
from pathlib import Path
import re

# Only capitalised tokens count: the trade boards' data-fields JSON also starts with "[[".
PLACEHOLDER = re.compile(r"\[\[[A-Z][^\]]*\]\]")
SERVED = {".html", ".js", ".txt", ".xml", ".json", ".webmanifest"}
SKIP_DIRS = {"tools", "node_modules"}


def find(root):
    hits = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or path.suffix not in SERVED or rel.parts[0] in SKIP_DIRS:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            hits += [(rel.as_posix(), n, tok) for tok in PLACEHOLDER.findall(line)]
    return hits


def main(argv):
    warn = "--warn-only" in argv
    args = [a for a in argv if not a.startswith("--")]
    root = Path(args[0]).resolve() if args else Path(__file__).resolve().parent.parent
    hits = find(root)
    if not hits:
        print("No legal placeholders in the served pages.")
        return 0
    level = "warning" if warn else "error"
    for rel, n, tok in hits:
        # GitHub Actions annotation, so the token shows on the run's summary page.
        print(f"::{level} file=website/{rel},line={n}::Legal placeholder {tok[:60]} must be filled in before launch")
    print(f"{len(hits)} legal placeholder(s) left. Fill them in under website/tools/legal/ and rerun build_pages.py.")
    return 0 if warn else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
