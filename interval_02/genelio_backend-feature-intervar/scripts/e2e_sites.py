"""End-to-end test of the Django chat pipeline across all microbiome sites.

For each body site (oral / skin / vaginal — and gut if the fixture is
present) this script:

1. Signs up a fresh user (per-site so transcripts stay isolated)
2. Uploads the matching ``~/Downloads/<Site> Report from Arshit Arora.pdf``
3. Polls until analysis finishes and prints a parsed-data digest
4. Creates a chat session bound to the report
5. Asks 5 site-appropriate questions
6. Saves the parsed data + transcript to ``scripts/_out_<site>.json``

Verifying that the assistant's reply cites real species / conditions /
diversity numbers from the PDF is a manual eyeball check on the printed
output (the parsed-data digest is printed alongside so you can compare).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import requests

BASE = os.getenv("GENELIO_BASE", "http://localhost:8000")
DOWNLOADS = Path.home() / "Downloads"


# ---------------------------------------------------------------------------
# Per-site fixtures + question sets
# ---------------------------------------------------------------------------

# The questions are designed to force the assistant to reach into the
# parsed report rather than reply with generic platitudes — each one
# names a structured field we know the analyzer extracted.

_SITE_PLAN = {
    "oral": {
        "fixture_fragments": ("oral", "report"),
        "fallback_paths": ["Oral Report from Arshit Arora.pdf"],
        "questions": [
            "Is my oral microbiome healthy overall?",
            "What is my Shannon Diversity score and what does it mean?",
            "Tell me about my obesity-related markers from this oral report — list each marker, abundance, range and status.",
            "Which oral organisms are above their healthy range, and why does it matter?",
            "Do I have any cancer-associated microbial markers flagged?",
        ],
    },
    "skin": {
        "fixture_fragments": ("skin", "report"),
        "fallback_paths": ["Skin Report from Arshit Arora.pdf"],
        "questions": [
            "Is my skin microbiome healthy overall?",
            "What is my Shannon Diversity score for skin and how should I interpret it?",
            "Tell me about my atopic dermatitis markers — list every one with its abundance, reference range and status.",
            "Which keystone skin organisms am I missing or low in?",
            "Tell me about my acne and psoriasis markers in this report.",
        ],
    },
    "vaginal": {
        "fixture_fragments": ("vaginal", "report"),
        "fallback_paths": ["Vaginal Report from Arshit Arora.pdf"],
        "questions": [
            "Is my vaginal microbiome healthy overall?",
            "What is my Shannon Diversity score and what does it mean for vaginal health?",
            "Tell me about my bacterial vaginosis markers — list each one with abundance and range.",
            "Do I have any sexually-transmitted-infection markers flagged in this report?",
            "What about miscarriage and infertility markers — what does my report show?",
        ],
    },
    "gut": {
        "fixture_fragments": ("gut",),
        "fallback_paths": ["gut.pdf"],
        "questions": [
            "Is my gut microbiome healthy overall?",
            "Tell me about my Shannon Diversity score — what does it mean for me?",
            "Which keystone species am I missing, and what foods can help?",
            "Tell me about my depression markers — list every one with its range and status.",
            "Do I have any pathogens flagged in this report?",
        ],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def pretty(obj, max_chars: int = 2000) -> str:
    s = json.dumps(obj, indent=2, default=str)
    return s if len(s) <= max_chars else s[:max_chars] + f"\n… [+{len(s) - max_chars} chars truncated]"


def find_fixture(fragments: Iterable[str], fallbacks: Iterable[str]) -> Path | None:
    for fb in fallbacks:
        candidate = DOWNLOADS / fb
        if candidate.exists():
            return candidate
    fragments_lower = [f.lower() for f in fragments]
    for pdf in DOWNLOADS.glob("*.pdf"):
        name = pdf.name.lower()
        if all(f in name for f in fragments_lower):
            return pdf
    return None


def signup_and_login(site: str) -> tuple[str, dict]:
    """Sign up a fresh per-site user and return (email, auth_headers)."""
    email = f"e2e+{site}+{int(time.time())}@example.com"
    password = "Sup3rSecret!"

    step(f"[{site}] 1. Signup ({email})")
    r = requests.post(f"{BASE}/auth/users/", json={
        "email": email,
        "first_name": "E2E",
        "last_name": site.title(),
        "password": password,
        "re_password": password,
        "agreed_to_terms": True,
    }, timeout=10)
    print(f"   {r.status_code} {r.reason}")
    if r.status_code != 201:
        print(pretty(r.json()))
        raise SystemExit(1)

    step(f"[{site}] 2. Login")
    r = requests.post(f"{BASE}/auth/jwt/create/", json={
        "email": email, "password": password,
    }, timeout=10)
    r.raise_for_status()
    access = r.json()["access"]
    print(f"   got access token ({len(access)} chars)")
    return email, {"Authorization": f"Bearer {access}"}


def upload_report(site: str, pdf: Path, auth: dict) -> tuple[dict, float]:
    step(f"[{site}] 3. Upload {pdf.name} ({pdf.stat().st_size:,} bytes)")
    t0 = time.perf_counter()
    with pdf.open("rb") as fh:
        r = requests.post(
            f"{BASE}/chatbot/report/data/",
            headers=auth,
            data={"report_type": site},
            files={"file": (pdf.name, fh, "application/pdf")},
            timeout=600,
        )
    elapsed = time.perf_counter() - t0
    print(f"   {r.status_code} in {elapsed:.1f}s")
    if r.status_code != 201:
        print(pretty(r.json()))
        raise SystemExit(1)
    return r.json(), elapsed


def print_parsed_digest(site: str, parsed_data: dict | None) -> None:
    if not isinstance(parsed_data, dict):
        print(f"   parsed_data is {type(parsed_data).__name__}")
        return
    rep = parsed_data.get("report") or {}
    print(f"   parsed_data keys: {sorted(parsed_data.keys())}")
    print(f"   patient:    {rep.get('patient')}")
    print(f"   diversity:  {rep.get('diversity')}")
    if site == "gut":
        print(f"   fb_ratio:   {rep.get('fb_ratio')}")
        present = rep.get("keystone_present", [])
        missing = rep.get("keystone_missing", [])
        print(f"   keystones:  {len(present)} present / {len(missing)} missing")
    else:
        organisms = rep.get("top_organisms", []) or []
        print(f"   organisms:  {len(organisms)}")
        for org in organisms[:3]:
            print(
                f"     - {org.get('name')}: "
                f"{org.get('abundance_raw')} (range {org.get('reference_raw')}) "
                f"-> {org.get('status')}"
            )
    conds = rep.get("conditions", {}) or {}
    print(f"   conditions: {len(conds)}")
    for name, data in list(conds.items())[:5]:
        markers = data.get("markers", []) if isinstance(data, dict) else []
        print(f"     - {name}: {len(markers)} marker(s)")


def run_site(site: str, plan: dict) -> dict | None:
    pdf = find_fixture(plan["fixture_fragments"], plan["fallback_paths"])
    if pdf is None:
        print(f"\n!!! [{site}] no fixture PDF found in {DOWNLOADS} — skipping")
        return None

    email, auth = signup_and_login(site)
    report, upload_elapsed = upload_report(site, pdf, auth)
    report_id = report["id"]
    print(f"   report_id: {report_id}  status: {report.get('status')}")

    parsed = report.get("parsed_data") or {}
    print_parsed_digest(site, parsed)

    step(f"[{site}] 4. Create chat session")
    r = requests.post(f"{BASE}/chatbot/chat/session/list/", headers=auth, json={
        "title": f"E2E {site} response check",
        "report_id": report_id,
    }, timeout=10)
    r.raise_for_status()
    session_id = r.json()["id"]
    print(f"   session_id: {session_id}")

    transcript = []
    for i, q in enumerate(plan["questions"], 1):
        step(f"[{site}] 5.{i} Q: {q}")
        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{BASE}/chatbot/chat/session/{session_id}/send/",
                headers=auth, json={"content": q}, timeout=600,
            )
        except requests.RequestException as exc:
            elapsed = time.perf_counter() - t0
            print(f"   transport error after {elapsed:.1f}s: {exc}")
            transcript.append({"q": q, "a": f"TRANSPORT ERROR: {exc}", "elapsed": elapsed})
            continue
        elapsed = time.perf_counter() - t0
        if r.status_code != 201:
            print(f"   {r.status_code} {r.reason}")
            try:
                print(pretty(r.json()))
            except Exception:
                print(r.text[:500])
            transcript.append({"q": q, "a": f"ERROR {r.status_code}", "elapsed": elapsed})
            continue
        body = r.json()
        assistant = body.get("assistant") or {}
        content = assistant.get("content") or ""
        print(f"   [{elapsed:.1f}s] assistant reply ({len(content)} chars):")
        print("   " + content.replace("\n", "\n   "))
        transcript.append({"q": q, "a": content, "elapsed": elapsed})

    out = Path(f"scripts/_out_{site}.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "site": site,
        "email": email,
        "pdf": str(pdf),
        "report_id": report_id,
        "session_id": session_id,
        "upload_seconds": upload_elapsed,
        "parsed_data_report": parsed.get("report") if isinstance(parsed, dict) else None,
        "transcript": transcript,
    }, indent=2, default=str))
    print(f"\nSaved transcript -> {out}")

    return {
        "site": site,
        "report_id": report_id,
        "questions": len(transcript),
        "errors": sum(1 for t in transcript if t["a"].startswith(("ERROR", "TRANSPORT"))),
        "transcript_path": str(out),
    }


def main() -> int:
    # CLI: ./e2e_sites.py [oral skin vaginal gut]   (default: oral skin vaginal)
    requested = sys.argv[1:] or ["oral", "skin", "vaginal"]
    unknown = [s for s in requested if s not in _SITE_PLAN]
    if unknown:
        print(f"unknown site(s): {unknown}; valid: {sorted(_SITE_PLAN)}", file=sys.stderr)
        return 2

    summary = []
    for site in requested:
        result = run_site(site, _SITE_PLAN[site])
        if result is not None:
            summary.append(result)

    step("SUMMARY")
    if not summary:
        print("no sites ran")
        return 1
    for s in summary:
        print(
            f"  {s['site']:8s}  questions={s['questions']}  errors={s['errors']}  "
            f"-> {s['transcript_path']}"
        )
    return 0 if all(s["errors"] == 0 for s in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
