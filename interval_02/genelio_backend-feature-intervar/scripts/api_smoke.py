"""End-to-end smoke test that exercises every public endpoint once.

Walks the auth + reports + chatbot APIs in dependency order, captures the
exact request payload and response body for each call, and writes the
whole transcript to ``scripts/_api_smoke.json`` plus a human-readable
markdown summary at ``scripts/_api_smoke.md``.

Designed to be safe to re-run — each invocation creates a brand-new
user (``api+smoke+<ts>@example.com``) so it never collides with
existing data.

Endpoints exercised
-------------------
Auth
  POST   /auth/users/                  (signup)
  POST   /auth/jwt/create/             (login)
  POST   /auth/jwt/verify/
  POST   /auth/jwt/refresh/
  GET    /auth/users/me/
  PATCH  /auth/users/me/
  POST   /auth/users/set_password/
  POST   /auth/users/set_avatar/

Reports
  GET    /chatbot/report/types/        (public)
  POST   /chatbot/report/data/         (upload)
  GET    /chatbot/report/data/         (list)
  GET    /chatbot/report/data/<id>/    (detail)

Chat
  POST   /chatbot/chat/session/list/   (create)
  GET    /chatbot/chat/session/list/   (list)
  GET    /chatbot/chat/session/<uuid>/ (detail)
  PATCH  /chatbot/chat/session/<uuid>/ (rename)
  POST   /chatbot/chat/session/<uuid>/send/

Cleanup
  DELETE /chatbot/chat/session/<uuid>/
  DELETE /chatbot/report/data/<id>/

Schema
  GET    /schema/                      (public)
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

BASE = os.getenv("GENELIO_BASE", "http://localhost:8000")
DOWNLOADS = Path.home() / "Downloads"
PDF_FIXTURE = DOWNLOADS / "Oral Report from Arshit Arora.pdf"


# ---------------------------------------------------------------------------
# Recorder — every endpoint hit goes through here so we can dump the full
# audit trail at the end.
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.failures: list[str] = []

    def record(self, label: str, method: str, url: str, *,
               request: Any, response: requests.Response,
               expected: int | tuple[int, ...] = 200,
               elapsed: float = 0.0) -> dict | None:
        if isinstance(expected, int):
            expected = (expected,)
        ok = response.status_code in expected
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:500]

        entry = {
            "label": label,
            "method": method,
            "url": url,
            "status_code": response.status_code,
            "expected": list(expected),
            "ok": ok,
            "elapsed_s": round(elapsed, 3),
            "request": _redact(request),
            "response": _truncate(body),
        }
        self.calls.append(entry)
        marker = "✅" if ok else "❌"
        print(f"{marker} {method:6s} {url} -> {response.status_code} ({elapsed:.2f}s)")
        if not ok:
            self.failures.append(f"{method} {url} -> {response.status_code}")
        return body if isinstance(body, dict) else None


def _redact(obj: Any) -> Any:
    """Redact passwords / tokens before persisting to disk."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = k.lower()
            if any(s in kl for s in ("password", "token", "authorization")):
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _truncate(obj: Any, max_chars: int = 4000) -> Any:
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    if len(s) <= max_chars:
        return obj
    return {"_truncated": True, "_preview": s[:max_chars] + "…"}


# ---------------------------------------------------------------------------
# Tiny helpers around requests so timing + recording is consistent.
# ---------------------------------------------------------------------------

def call(rec: Recorder, label: str, method: str, path: str, *,
         headers: dict | None = None, json_body: Any = None,
         data: dict | None = None, files: dict | None = None,
         expected: int | tuple[int, ...] = 200,
         timeout: int = 600) -> tuple[requests.Response, dict | None]:
    url = f"{BASE}{path}"
    request_payload: Any = json_body if json_body is not None else data
    if files:
        request_payload = {**(data or {}), "_files": list(files.keys())}
    t0 = time.perf_counter()
    r = requests.request(
        method, url,
        headers=headers, json=json_body, data=data, files=files,
        timeout=timeout,
    )
    elapsed = time.perf_counter() - t0
    body = rec.record(label, method, path,
                      request=request_payload, response=r,
                      expected=expected, elapsed=elapsed)
    return r, body


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main() -> int:
    if not PDF_FIXTURE.exists():
        print(f"!!! fixture not found: {PDF_FIXTURE}", file=sys.stderr)
        return 2

    rec = Recorder()
    ts = int(time.time())
    email = f"api+smoke+{ts}@example.com"
    password = "Sup3rSecret!"
    new_password = "Even-M0re-Secret!"

    print(f"\n=== AUTH ===")

    # 1) Signup
    _, body = call(rec, "signup", "POST", "/auth/users/",
        json_body={
            "email": email, "first_name": "Smoke", "last_name": "Test",
            "password": password, "re_password": password,
            "agreed_to_terms": True,
        }, expected=201)

    # 2) Login -> tokens
    _, body = call(rec, "login", "POST", "/auth/jwt/create/",
        json_body={"email": email, "password": password}, expected=200)
    access = body["access"]
    refresh = body["refresh"]
    auth = {"Authorization": f"Bearer {access}"}

    # 3) Verify
    call(rec, "jwt-verify", "POST", "/auth/jwt/verify/",
        json_body={"token": access}, expected=200)

    # 4) Refresh
    _, body = call(rec, "jwt-refresh", "POST", "/auth/jwt/refresh/",
        json_body={"refresh": refresh}, expected=200)
    access = body["access"]
    auth = {"Authorization": f"Bearer {access}"}

    # 5) GET me
    call(rec, "users-me-get", "GET", "/auth/users/me/", headers=auth, expected=200)

    # 6) PATCH me
    call(rec, "users-me-patch", "PATCH", "/auth/users/me/",
        headers=auth, json_body={"first_name": "Smoky"}, expected=200)

    # 7) set_avatar — build a real 1x1 PNG via Pillow so Django's
    # ImageField validator accepts it.
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(0, 128, 255)).save(buf, format="PNG")
    buf.seek(0)
    call(rec, "set_avatar", "POST", "/auth/users/set_avatar/",
        headers=auth,
        files={"avatar": ("pixel.png", buf, "image/png")},
        expected=200)

    # 8) set_password
    call(rec, "set_password", "POST", "/auth/users/set_password/",
        headers=auth,
        json_body={"new_password": new_password, "current_password": password},
        expected=(204, 200))

    # Re-login with new password so the rest of the flow uses a fresh token
    _, body = call(rec, "login-after-pw-change", "POST", "/auth/jwt/create/",
        json_body={"email": email, "password": new_password}, expected=200)
    auth = {"Authorization": f"Bearer {body['access']}"}

    print(f"\n=== REPORTS ===")

    # 9) Public catalogue
    call(rec, "report-types", "GET", "/chatbot/report/types/", expected=200)

    # 10) Upload PDF
    print(f"   uploading {PDF_FIXTURE.name} ({PDF_FIXTURE.stat().st_size:,} B)…")
    with PDF_FIXTURE.open("rb") as fh:
        _, body = call(rec, "report-upload", "POST", "/chatbot/report/data/",
            headers=auth,
            data={"report_type": "oral"},
            files={"file": (PDF_FIXTURE.name, fh, "application/pdf")},
            expected=201, timeout=600)
    report_id = body["id"]

    # 11) List reports
    call(rec, "report-list", "GET", "/chatbot/report/data/",
        headers=auth, expected=200)

    # 12) Filtered list
    call(rec, "report-list-filter", "GET",
        "/chatbot/report/data/?report_type=oral",
        headers=auth, expected=200)

    # 13) Detail
    call(rec, "report-detail", "GET", f"/chatbot/report/data/{report_id}/",
        headers=auth, expected=200)

    print(f"\n=== CHAT ===")

    # 14) Create session
    _, body = call(rec, "session-create", "POST", "/chatbot/chat/session/list/",
        headers=auth,
        json_body={"title": "API smoke test", "report_id": report_id},
        expected=201)
    session_id = body["id"]

    # 15) List sessions
    call(rec, "session-list", "GET", "/chatbot/chat/session/list/",
        headers=auth, expected=200)

    # 16) Detail
    call(rec, "session-detail", "GET", f"/chatbot/chat/session/{session_id}/",
        headers=auth, expected=200)

    # 17) Rename
    call(rec, "session-rename", "PATCH", f"/chatbot/chat/session/{session_id}/",
        headers=auth, json_body={"title": "API smoke renamed"},
        expected=200)

    # 18) Send message (LLM round-trip)
    print("   sending one chat message — this hits vLLM…")
    call(rec, "session-send", "POST",
        f"/chatbot/chat/session/{session_id}/send/",
        headers=auth,
        json_body={"content": "Give me a one-sentence summary of my oral microbiome health."},
        expected=201, timeout=600)

    print(f"\n=== SCHEMA ===")
    # 19) OpenAPI schema (public)
    call(rec, "openapi-schema", "GET", "/schema/", expected=200)

    print(f"\n=== CLEANUP ===")
    # 20) Delete session
    call(rec, "session-delete", "DELETE", f"/chatbot/chat/session/{session_id}/",
        headers=auth, expected=204)

    # 21) Delete report
    call(rec, "report-delete", "DELETE", f"/chatbot/report/data/{report_id}/",
        headers=auth, expected=204)

    # ---------------------------------------------------------------- write
    out_json = Path("scripts/_api_smoke.json")
    out_md = Path("scripts/_api_smoke.md")
    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps({
        "base_url": BASE,
        "user_email": email,
        "report_id": report_id,
        "session_id": session_id,
        "calls": rec.calls,
        "failures": rec.failures,
    }, indent=2, default=str))
    _write_markdown(out_md, rec)

    print("\n=== SUMMARY ===")
    print(f"calls:    {len(rec.calls)}")
    print(f"failures: {len(rec.failures)}")
    if rec.failures:
        for f in rec.failures:
            print(f"  - {f}")
    print(f"\nFull JSON  -> {out_json}")
    print(f"Markdown   -> {out_md}")
    return 0 if not rec.failures else 1


def _write_markdown(path: Path, rec: Recorder) -> None:
    lines: list[str] = ["# API Smoke Test Results", ""]
    lines.append(f"- Base URL: `{BASE}`")
    lines.append(f"- Total calls: **{len(rec.calls)}**")
    lines.append(f"- Failures: **{len(rec.failures)}**")
    lines.append("")
    lines.append("| # | Endpoint | Method | Status | Elapsed |")
    lines.append("|---|----------|--------|--------|---------|")
    for i, c in enumerate(rec.calls, 1):
        marker = "✅" if c["ok"] else "❌"
        lines.append(
            f"| {i} | `{c['url']}` | {c['method']} | "
            f"{marker} {c['status_code']} | {c['elapsed_s']}s |"
        )
    lines.append("")
    for c in rec.calls:
        lines.append(f"## {c['method']} `{c['url']}`")
        lines.append(f"_{c['label']}_")
        lines.append("")
        lines.append(f"- Status: **{c['status_code']}** (expected {c['expected']})")
        lines.append(f"- Elapsed: {c['elapsed_s']}s")
        lines.append("")
        lines.append("**Request**")
        lines.append("```json")
        lines.append(json.dumps(c["request"], indent=2, default=str))
        lines.append("```")
        lines.append("**Response**")
        lines.append("```json")
        lines.append(json.dumps(c["response"], indent=2, default=str)[:3000])
        lines.append("```")
        lines.append("")
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
