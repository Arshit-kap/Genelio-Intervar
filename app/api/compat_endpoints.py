"""
Genelio Frontend Compatibility Layer
Exposes Django-style API routes expected by the Next.js frontend:

  POST /auth/jwt/create/              → mock JWT auth (any credentials accepted)
  POST /auth/users/                   → register (mock)
  GET  /auth/users/me/                → current user
  GET  /chatbot/chat/session/list/    → list chat sessions
  POST /chatbot/chat/session/list/    → create session
  GET  /chatbot/chat/session/{id}/    → session detail + messages
  PATCH /chatbot/chat/session/{id}/   → rename session
  POST /chatbot/chat/session/{id}/send/ → send message (wired to 6-layer pipeline)
  GET  /chatbot/report/data/          → list reports
  POST /chatbot/report/data/          → upload report (metadata only, DB is pre-loaded)
  GET  /chatbot/report/data/{id}/     → report detail

All session/user state is held in memory (no extra DB tables needed).
Auth is intentionally open — any email/password returns a valid session token.
"""
import uuid
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, UploadFile, File, Form

router = APIRouter(tags=["Genelio Frontend"])

# ── Thread-safe in-memory stores ───────────────────────────────────────────────
_lock     = threading.Lock()
_users    : dict = {}    # token → user dict
_sessions : dict = {}    # session_id → session dict
_reports  : dict = {}    # report_id → report dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_user(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[7:]
    with _lock:
        user = _users.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user, token


# ── Auth ───────────────────────────────────────────────────────────────────────

@router.post("/auth/jwt/create/")
@router.post("/auth/token/jwt/create/")
async def jwt_create(request: Request):
    """Accept any credentials (form or JSON) and return a UUID bearer token."""
    email = "user@genelio.local"
    try:
        body = await request.form()
        email = body.get("email", email)
    except Exception:
        try:
            body = await request.json()
            email = body.get("email", email)
        except Exception:
            pass

    token = str(uuid.uuid4())
    with _lock:
        _users[token] = {
            "id": "1",
            "uuid": str(uuid.uuid4()),
            "email": email,
            "first_name": "Genelio",
            "last_name": "User",
            "default_language_code": "en",
            "default_timezone": "UTC",
            "avatar": None,
            "profile_picture": None,
        }
    return {"access": token, "refresh": str(uuid.uuid4())}


@router.post("/auth/users/")
async def register(request: Request):
    body = await request.json()
    return {
        "status": 201,
        "data": {
            "id": 1,
            "email": body.get("email", ""),
            "username": body.get("email", ""),
            "first_name": body.get("first_name", ""),
            "last_name": body.get("last_name", ""),
        },
        "message": "Account created. Please log in.",
    }


@router.post("/auth/users/forgot_password/")
async def forgot_password(request: Request):
    return {"message": "If the email exists, a password reset link will be sent."}


@router.post("/auth/users/reset_password/")
async def reset_password(request: Request):
    return {"message": "Password reset successful. Please log in."}


@router.get("/auth/users/me/")
async def get_me(authorization: str = Header(default="")):
    user, _ = _require_user(authorization)
    return user


# ── Reports ────────────────────────────────────────────────────────────────────

@router.get("/chatbot/report/data/")
async def list_reports(authorization: str = Header(default="")):
    user, _ = _require_user(authorization)
    with _lock:
        user_reports = [r for r in _reports.values() if r.get("user_id") == user["id"]]
    return {"chat_sessions": user_reports, "status": 200}


@router.post("/chatbot/report/data/")
async def upload_report(
    authorization: str = Header(default=""),
    file: UploadFile = File(...),
    report_type: str = Form(...),
):
    """Accept the uploaded file (metadata only — the genomic DB is already loaded)."""
    user, _ = _require_user(authorization)
    report_id = str(uuid.uuid4())
    now = _now()
    report = {
        "id": report_id,
        "report_type": report_type,
        "original_filename": file.filename or "report",
        "status": "ready",
        "enrichment_status": "none",
        "enrichment_updated_at": None,
        "enrichment_error": "",
        "file_url": None,
        "parsed_data": {"status": "ok", "filename": file.filename, "report_type": report_type},
        "enrichment_data": None,
        "created_at": now,
        "updated_at": now,
        "user_id": user["id"],
        # Legacy ReportSession shape
        "file": file.filename or "report",
        "session_id": None,
        "user": 1,
    }
    with _lock:
        _reports[report_id] = report
    return report


@router.get("/chatbot/report/data/{report_id}/")
async def get_report_detail(report_id: str, authorization: str = Header(default="")):
    _require_user(authorization)
    with _lock:
        report = _reports.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/chatbot/report/data/{report_id}/enrich/")
async def enrich_report(report_id: str, authorization: str = Header(default="")):
    _require_user(authorization)
    with _lock:
        if report_id in _reports:
            _reports[report_id]["enrichment_status"] = "ready"
    return {"queued": True, "enrichment_status": "ready"}


# ── Session helpers ────────────────────────────────────────────────────────────

def _session_summary(s: dict) -> dict:
    """Serialise a session for list view (no messages array)."""
    return {
        "id":            s["id"],
        "uid":           s["id"],
        "title":         s.get("title") or "New Chat",
        "name":          s.get("title") or "New Chat",
        "report":        s.get("report"),
        "report_type":   s.get("report_type", "wgs"),
        "message_count": len(s.get("messages", [])),
        "created_at":    s.get("created_at", ""),
        "updated_at":    s.get("updated_at", ""),
        "is_active":     True,
        "new":           0,
    }


# ── Chat sessions ──────────────────────────────────────────────────────────────

@router.get("/chatbot/chat/session/list/")
async def list_sessions(authorization: str = Header(default=""), page: int = 1):
    user, _ = _require_user(authorization)
    with _lock:
        user_sessions = sorted(
            [s for s in _sessions.values() if s.get("user_id") == user["id"]],
            key=lambda s: s.get("updated_at", ""),
            reverse=True,
        )
    results = [_session_summary(s) for s in user_sessions]
    return {"count": len(results), "next": None, "previous": None, "results": results}


@router.post("/chatbot/chat/session/list/")
async def create_session(request: Request, authorization: str = Header(default="")):
    user, _ = _require_user(authorization)
    body = await request.json()
    session_id = str(uuid.uuid4())
    now = _now()

    report_id  = body.get("report_id")
    report_info = None
    if report_id:
        with _lock:
            r = _reports.get(report_id)
        if r:
            report_info = {"id": report_id, "report_type": r["report_type"], "status": r["status"]}
            with _lock:
                _reports[report_id]["session_id"] = session_id

    session = {
        "id":          session_id,
        "uid":         session_id,
        "title":       body.get("title") or "New Chat",
        "name":        body.get("title") or "New Chat",
        "report":      report_info,
        "report_type": report_info["report_type"] if report_info else "wgs",
        "messages":    [],
        "created_at":  now,
        "updated_at":  now,
        "user_id":     user["id"],
        "is_active":   True,
    }
    with _lock:
        _sessions[session_id] = session
    return _session_summary(session)


@router.get("/chatbot/chat/session/{session_id}/")
async def get_session(session_id: str, authorization: str = Header(default="")):
    _require_user(authorization)
    with _lock:
        session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {**_session_summary(session), "messages": session.get("messages", [])}


@router.patch("/chatbot/chat/session/{session_id}/")
async def rename_session(
    session_id: str,
    request: Request,
    authorization: str = Header(default=""),
):
    _require_user(authorization)
    body = await request.json()
    with _lock:
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        s = _sessions[session_id]
        s["title"] = body.get("title", s["title"])
        s["name"]  = s["title"]
        s["updated_at"] = _now()
    return _session_summary(s)


@router.post("/chatbot/chat/session/{session_id}/send/")
async def send_message(
    session_id: str,
    request: Request,
    authorization: str = Header(default=""),
):
    """
    Core chat endpoint — receives a message from the frontend and routes it
    through the 6-layer genomic pipeline (_run_chat), returns Django-shaped response.
    """
    _require_user(authorization)
    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    with _lock:
        session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build conversation history from stored messages
    from app.api.ai_endpoints import ChatRequest, ChatMessage as APIChatMsg, _run_chat
    history = [
        APIChatMsg(role=m["role"], content=m["content"])
        for m in session.get("messages", [])
    ]

    # Run through the 6-layer pipeline
    from fastapi.concurrency import run_in_threadpool
    chat_req = ChatRequest(message=content, history=history, max_rows=20, include_sql=True)
    result = await run_in_threadpool(_run_chat, chat_req)

    now = _now()
    user_msg = {
        "id":         str(uuid.uuid4()),
        "role":       "user",
        "content":    content,
        "created_at": now,
    }
    assistant_msg = {
        "id":         str(uuid.uuid4()),
        "role":       "assistant",
        "content":    result.response,
        "created_at": now,
    }

    with _lock:
        s = _sessions[session_id]
        s["messages"].append(user_msg)
        s["messages"].append(assistant_msg)
        s["updated_at"] = now
        # Auto-title session on first exchange
        if len(s["messages"]) == 2:
            s["title"] = content[:60]
            s["name"]  = s["title"]

    return {"user": user_msg, "assistant": assistant_msg}


# ── Franklin / variant lookup stub ────────────────────────────────────────────

@router.get("/franklin/search/")
async def franklin_search(
    q: str = "",
    ref: str = "hg19",
    mode: str = "auto",
    authorization: str = Header(default=""),
):
    """Stub — returns informative message; full Franklin API not configured."""
    _require_user(authorization)
    return {
        "kind": "gene",
        "query": q,
        "reference": ref,
        "page_url": None,
        "error": None,
        "sections": {
            "info": {
                "key": "info",
                "label": "InterVar Genomic Engine",
                "url": "",
                "status": 200,
                "data": {
                    "message": (
                        f"Variant/gene lookup for '{q}'. "
                        "Use the chat interface to query this variant "
                        "against the InterVar database."
                    )
                },
                "error": None,
            }
        },
        "_cache": "miss",
    }
