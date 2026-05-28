"""Quick parser sanity check against the real oral/skin/vaginal PDFs.

Runs each analyzer and prints:
  * whether parsing succeeded
  * the diversity value + range + status
  * # top organisms, # keystones
  * conditions with marker counts

Used during development to verify the pattern-driven parser lands on the
right numbers without needing the full Django stack running.
"""
from __future__ import annotations

import sys
from pathlib import Path

import os

# Bootstrap Django so `from reports...` imports resolve. We also make the
# repo root importable for direct `python scripts/check_parsers.py` calls.
_REPO = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(_REPO))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "genelio.settings")
import django  # noqa: E402
django.setup()

from reports.analyzers import oral, skin, vaginal  # noqa: E402

CASES = [
    ("oral", "~/Downloads/Oral Report from Arshit Arora.pdf", oral),
    ("skin", "~/Downloads/Skin Report from Arshit Arora.pdf", skin),
    ("vaginal", "~/Downloads/Vaginal Report from Arshit Arora.pdf", vaginal),
]


def summary(result: dict) -> None:
    rep = result.get("report", {})
    print(f"  status: {result.get('status')}")
    print(f"  site:   {result.get('site')}")
    print(f"  patient fields: {len(rep.get('patient', {}))}  {list(rep.get('patient', {}).keys())}")
    print(f"  diversity: {rep.get('diversity')}")
    print(f"  top_organisms: {len(rep.get('top_organisms', []))}")
    for org in rep.get("top_organisms", [])[:3]:
        print(f"     - {org['name']:40s}  {org['abundance_raw']:>10s}  ref {org.get('reference') or 'ND':>15s}  [{org['status']}]")
    print(f"  keystones: {len(rep.get('keystone_species', []))}")
    print(f"  conditions: {len(rep.get('conditions', {}))}")
    for name, data in rep.get("conditions", {}).items():
        n = len(data.get("markers", []))
        oor = sum(1 for m in data["markers"] if m["status"] in ("above_range", "below_range"))
        print(f"     - {name:40s}  {n} markers   ({oor} out of range)")
    ctx = result.get("analysis_context", "")
    print(f"  analysis_context: {len(ctx)} chars")


def main() -> int:
    for label, path_s, mod in CASES:
        path = Path(path_s).expanduser()
        print(f"\n{'=' * 78}\n{label.upper()}  ({path.name})\n{'=' * 78}")
        if not path.exists():
            print(f"  MISSING: {path}")
            continue
        try:
            result = mod.analyze(str(path))
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
            continue
        summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
