"""End-to-end test of the Django gut-microbiome chat pipeline.

1. Sign up a fresh user
2. Upload ~/Downloads/gut.pdf as a gut-type report
3. Poll until analysis finishes
4. Create a chat session linked to the report
5. Ask a canonical question and print the assistant reply

Prints enough metadata to tell whether the RAG pipeline actually ran
(Chroma index built at upload, parsed_data has real fields, chat reply
is grounded rather than the stub fallback).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

BASE = os.getenv("GENELIO_BASE", "http://localhost:8000")
PDF = Path(os.getenv("GUT_PDF", str(Path.home() / "Downloads" / "gut.pdf")))
EMAIL = f"e2e+{int(time.time())}@example.com"
PASSWORD = "Sup3rSecret!"


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def pretty(obj, max_chars: int = 2000) -> str:
    s = json.dumps(obj, indent=2, default=str)
    return s if len(s) <= max_chars else s[:max_chars] + f"\n… [+{len(s)-max_chars} chars truncated]"


def main() -> int:
    if not PDF.exists():
        print(f"PDF not found at {PDF}", file=sys.stderr)
        return 2

    step("1. Signup")
    r = requests.post(f"{BASE}/auth/users/", json={
        "email": EMAIL,
        "first_name": "E2E",
        "last_name": "Tester",
        "password": PASSWORD,
        "re_password": PASSWORD,
        "agreed_to_terms": True,
    }, timeout=10)
    print(f"   {r.status_code} {r.reason}")
    print(f"   user id: {r.json().get('id')}")

    step("2. Login")
    r = requests.post(f"{BASE}/auth/jwt/create/", json={
        "email": EMAIL, "password": PASSWORD,
    }, timeout=10)
    r.raise_for_status()
    access = r.json()["access"]
    auth = {"Authorization": f"Bearer {access}"}
    print(f"   got access token ({len(access)} chars)")

    step(f"3. Upload {PDF.name} ({PDF.stat().st_size:,} bytes)")
    t0 = time.perf_counter()
    with PDF.open("rb") as fh:
        r = requests.post(
            f"{BASE}/chatbot/report/data/",
            headers=auth,
            data={"report_type": "gut"},
            files={"file": (PDF.name, fh, "application/pdf")},
            timeout=600,  # embedding a 1.3MB PDF can be slow on first run
        )
    upload_elapsed = time.perf_counter() - t0
    print(f"   {r.status_code} in {upload_elapsed:.1f}s")
    if r.status_code != 201:
        print(pretty(r.json()))
        return 1
    report = r.json()
    report_id = report["id"]
    print(f"   report_id: {report_id}")
    print(f"   status:    {report['status']}")
    pd = report.get("parsed_data") or {}
    print(f"   parsed_data keys: {sorted(pd.keys()) if isinstance(pd, dict) else type(pd).__name__}")
    if isinstance(pd, dict):
        rep = pd.get("report", {})
        if isinstance(rep, dict):
            print(f"   patient:     {rep.get('patient')}")
            print(f"   diversity:   {rep.get('diversity')}")
            print(f"   fb_ratio:    {rep.get('fb_ratio')}")
            present = rep.get("keystone_present", [])
            missing = rep.get("keystone_missing", [])
            print(f"   keystones:   {len(present)} present / {len(missing)} missing")
            conds = rep.get("conditions", {})
            print(f"   conditions:  {len(conds)}")

    step("4. Create chat session linked to the report")
    r = requests.post(f"{BASE}/chatbot/chat/session/list/", headers=auth, json={
        "title": "E2E gut comparison",
        "report_id": report_id,
    }, timeout=10)
    r.raise_for_status()
    session_id = r.json()["id"]
    print(f"   session_id: {session_id}")

    questions = [
        "Is my gut microbiome healthy overall?",
        "Tell me about my Shannon Diversity score — what does it mean for me?",
        "Which keystone species am I missing, and what foods can help?",
        "Tell me about my depression markers — list every one with its range and status.",
        "Do I have any pathogens flagged in this report?",
    ]

    transcript = []
    for i, q in enumerate(questions, 1):
        step(f"5.{i} Q: {q}")
        t0 = time.perf_counter()
        r = requests.post(
            f"{BASE}/chatbot/chat/session/{session_id}/send/",
            headers=auth, json={"content": q}, timeout=600,
        )
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
        assistant = body.get("assistant", {})
        content = assistant.get("content", "")
        print(f"   [{elapsed:.1f}s] assistant reply ({len(content)} chars):")
        print("   " + content.replace("\n", "\n   "))
        transcript.append({"q": q, "a": content, "elapsed": elapsed})

    out = Path("scripts/_out_django.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "email": EMAIL,
        "report_id": report_id,
        "session_id": session_id,
        "upload_seconds": upload_elapsed,
        "parsed_data_report": pd.get("report") if isinstance(pd, dict) else None,
        "transcript": transcript,
    }, indent=2, default=str))
    print(f"\nSaved transcript -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
