"""Side-by-side comparison of Django vs legacy pipeline responses.

Loads scripts/_out_django.json and scripts/_out_legacy.json (produced by
e2e_django.py and e2e_legacy.py respectively) and prints:
  * Parsed-report diff (same fields, same values?)
  * Per-question: latency, reply length, shared keywords (e.g. species
    names, biomarker status terms)
  * Full transcripts dumped to scripts/_compare.md for visual diffing
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path("scripts")
DJANGO = json.loads((OUT / "_out_django.json").read_text())
LEGACY = json.loads((OUT / "_out_legacy.json").read_text())

# Biomarker terms worth tracking across both responses so we can tell
# whether the two pipelines surface the same grounded facts.
KEY_TERMS = [
    # numeric / markers
    "4.742", "6.279", "Shannon", "F/B ratio",
    # keystone species
    "Bifidobacterium", "Clostridium butyricum", "Christensenella",
    "Faecalibacterium", "Akkermansia", "Roseburia",
    # depression markers
    "Holdemania", "Eggerthella", "Streptococcus", "Paraprevotella",
    # pathogens
    "Bacteroides fragilis", "Escherichia", "Methanobrevibacter",
    "Ruminococcus torques", "Eubacterium rectale", "Clostridium difficile",
    # status flags
    "above range", "within range", "not detected", "below range",
]


def find_terms(text: str) -> set[str]:
    low = text.lower()
    return {t for t in KEY_TERMS if t.lower() in low}


def fmt_set(s: set[str]) -> str:
    return ", ".join(sorted(s)) or "—"


def main() -> None:
    lines: list[str] = []
    out = lambda s="": (print(s), lines.append(s))  # noqa: E731

    out("# Django vs Legacy — Gut Microbiome Pipeline Comparison")
    out()
    out(f"**PDF**: `~/Downloads/gut.pdf`  ")
    out(f"**Embedder**: `nomic-ai/nomic-embed-text-v1.5` (both)  ")
    out(f"**LLM**: `qwen3-30b` via vLLM (both)  ")
    out(f"**Chunks**: 1200 / 200 overlap (both)  ")
    out()

    # ---- Parsed report diff --------------------------------------------
    out("## 1. Parsed report (structured extraction)")
    out()
    dr = DJANGO.get("parsed_data_report") or {}
    lr = LEGACY.get("parsed_report") or {}
    rows = [
        ("patient", dr.get("patient"), lr.get("patient")),
        ("diversity", dr.get("diversity"), lr.get("diversity")),
        ("fb_ratio", dr.get("fb_ratio"), lr.get("fb_ratio")),
        ("keystone_present (count)",
         len(dr.get("keystone_present", [])),
         len(lr.get("keystone_present", []))),
        ("keystone_missing (count)",
         len(dr.get("keystone_missing", [])),
         len(lr.get("keystone_missing", []))),
        ("conditions (count)",
         len(dr.get("conditions", {})),
         len(lr.get("conditions", {}))),
    ]
    out("| Field | Django | Legacy | Match |")
    out("|---|---|---|---|")
    for name, d, l in rows:
        match = "✅" if d == l else "❌"
        out(f"| {name} | `{d}` | `{l}` | {match} |")
    out()

    # ---- Per-question --------------------------------------------------
    out("## 2. Per-question comparison")
    out()
    out("| # | Question | Django latency | Legacy latency | Django chars | Legacy chars | Shared key terms | Django-only | Legacy-only |")
    out("|---|---|---|---|---|---|---|---|---|")

    for i, (d, l) in enumerate(zip(DJANGO["transcript"], LEGACY["transcript"]), 1):
        d_terms = find_terms(d["a"])
        l_terms = find_terms(l["a"])
        shared = d_terms & l_terms
        only_d = d_terms - l_terms
        only_l = l_terms - d_terms
        q_short = (d["q"][:60] + "…") if len(d["q"]) > 60 else d["q"]
        out(
            f"| {i} | {q_short} | {d['elapsed']:.1f}s | {l['elapsed']:.1f}s | "
            f"{len(d['a']):,} | {len(l['a']):,} | "
            f"{len(shared)} ({fmt_set(shared) if len(shared) <= 4 else f'{len(shared)} terms'}) | "
            f"{fmt_set(only_d) if len(only_d) <= 3 else f'{len(only_d)} terms'} | "
            f"{fmt_set(only_l) if len(only_l) <= 3 else f'{len(only_l)} terms'} |"
        )
    out()

    # ---- Full transcripts ---------------------------------------------
    out("## 3. Full transcripts")
    for i, (d, l) in enumerate(zip(DJANGO["transcript"], LEGACY["transcript"]), 1):
        out()
        out(f"### Q{i}: {d['q']}")
        out()
        out(f"<details><summary>Django reply ({len(d['a'])} chars, {d['elapsed']:.1f}s)</summary>")
        out()
        out(d["a"])
        out()
        out("</details>")
        out()
        out(f"<details><summary>Legacy reply ({len(l['a'])} chars, {l['elapsed']:.1f}s)</summary>")
        out()
        out(l["a"])
        out()
        out("</details>")

    out_path = OUT / "_compare.md"
    out_path.write_text("\n".join(lines))
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
