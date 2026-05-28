"""Dump structural summary of a microbiome PDF so we can build a parser.

Prints page headings, first 40 lines of each page's text, and the shape
of every table per page. Used one-off to understand the layout of the
oral / skin / vaginal reports.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pdfplumber


def inspect(path: Path, max_pages: int | None = None) -> None:
    print(f"\n{'=' * 78}\n{path.name}  ({path.stat().st_size:,} bytes)\n{'=' * 78}")
    with pdfplumber.open(path) as pdf:
        print(f"Pages: {len(pdf.pages)}")
        for i, page in enumerate(pdf.pages):
            if max_pages and i >= max_pages:
                break
            text = (page.extract_text() or "").strip()
            tables = page.extract_tables() or []
            first_line = text.split("\n")[0][:110] if text else "(empty)"
            print(f"\n--- Page {i+1}  |  first line: {first_line!r}  |  tables: {len(tables)}  ---")
            # Show first 30 lines of text to sense the section
            for line in text.split("\n")[:30]:
                print(f"  T  {line}")
            for j, tb in enumerate(tables):
                rows, cols = len(tb), max((len(r) for r in tb), default=0)
                print(f"  [Table {j+1}]  {rows}x{cols}")
                for row in tb[:6]:
                    print(f"     {row}")


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print("usage: inspect_pdf.py <pdf> [<pdf> ...]", file=sys.stderr)
        return 2
    for p in paths:
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr)
            continue
        inspect(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
