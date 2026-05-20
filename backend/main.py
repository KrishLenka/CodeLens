"""
CodeLens FastAPI backend.
Run with:  uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.project_env import load_project_env

load_project_env()

from backend.auth import (
    _COOKIE_NAME,
    _frontend_url,
    build_github_login_url,
    create_session_token,
    decode_session_token,
    exchange_code_for_user,
    get_current_user,
)
from backend.jobs import store as job_store
from backend.pipeline import (
    load_user_history,
    run_analysis_pipeline,
    save_analysis_to_history,
)
from tools.payments import (
    can_run_analysis,
    consume_use,
    create_checkout_session,
    get_user,
    get_or_create_user,
    process_successful_payment,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="CodeLens API", version="2.0.0")

_FRONTEND = os.getenv("FRONTEND_URL", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_FRONTEND],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_IS_PROD = os.getenv("ENV", "development") == "production"
_COOKIE_OPTS: dict[str, Any] = {
    "httponly": True,
    "samesite": "none" if _IS_PROD else "lax",
    "secure": _IS_PROD,
    "max_age": 60 * 60 * 24 * 30,  # 30 days
}

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.get("/api/auth/login")
def github_login(response: Response) -> RedirectResponse:
    login_url, state = build_github_login_url()
    resp = RedirectResponse(url=login_url)
    resp.set_cookie("oauth_state", state, httponly=True, max_age=600, samesite="lax")
    return resp


@app.get("/api/auth/callback")
def github_callback(
    request: Request,
    response: Response,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    oauth_state: str | None = Cookie(default=None),
) -> RedirectResponse:
    frontend = _frontend_url()

    if error:
        return RedirectResponse(url=f"{frontend}?auth_error={error}")

    if not code:
        return RedirectResponse(url=f"{frontend}?auth_error=missing_code")

    try:
        user = exchange_code_for_user(code)
        get_or_create_user(user["username"])
        token = create_session_token(user)
    except Exception as exc:
        return RedirectResponse(url=f"{frontend}?auth_error=oauth_failed")

    resp = RedirectResponse(url=frontend)
    resp.set_cookie(_COOKIE_NAME, token, **_COOKIE_OPTS)
    resp.delete_cookie("oauth_state")
    return resp


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(_COOKIE_NAME)
    return {"status": "logged_out"}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    username = user["username"]
    try:
        db_user = get_user(username) or {}
    except Exception:
        db_user = {}
    return {
        "username": username,
        "avatar_url": user.get("avatar_url", ""),
        "free_uses_remaining": int(db_user.get("free_uses_remaining", 0)),
        "paid_uses_remaining": int(db_user.get("paid_uses_remaining", 0)),
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _run_job(
    job_id: str,
    username: str,
    github_url: str,
    resume_bytes: bytes | None,
    resume_filename: str | None,
    job_description: str,
    company_github_url: str,
    use_reason: str,
) -> None:
    """Runs in a background thread."""
    try:
        result = run_analysis_pipeline(
            github_url=github_url,
            resume_bytes=resume_bytes,
            resume_filename=resume_filename,
            job_description=job_description,
            company_github_url=company_github_url,
            on_progress=lambda msg: job_store.update_progress(job_id, msg),
        )
        # Only bill after successful completion
        consume_use(username, use_reason)
        analysis_id = save_analysis_to_history(
            username=username,
            result=result,
            github_url=github_url,
            had_resume=resume_bytes is not None,
            had_jd=bool(job_description.strip()),
            resume_bytes=resume_bytes,
            resume_filename=resume_filename,
            job_description=job_description,
        )
        result["analysis_id"] = analysis_id
        job_store.complete(job_id, result)
    except Exception:
        job_store.fail(job_id, traceback.format_exc())


@app.post("/api/analyze")
async def start_analysis(
    background_tasks: BackgroundTasks,
    github_url: str = Form(...),
    job_description: str = Form(""),
    company_github_url: str = Form(""),
    resume: UploadFile | None = File(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    username = user["username"]

    allowed, reason = can_run_analysis(username)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No analyses remaining. Please purchase more.",
        )

    resume_bytes: bytes | None = None
    resume_filename: str | None = None
    if resume is not None:
        resume_bytes = await resume.read()
        resume_filename = resume.filename

    job_id = uuid.uuid4().hex
    job_store.create(job_id, username)

    thread = threading.Thread(
        target=_run_job,
        args=(
            job_id, username, github_url.strip(),
            resume_bytes, resume_filename,
            job_description, company_github_url.strip(),
            reason,
        ),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/analyze/{job_id}")
def poll_job(
    job_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.username != user["username"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    out = job.to_dict()
    # Strip the heavy result from polling responses — only include on done status
    if job.status != "done":
        out.pop("result", None)
    return out


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@app.get("/api/history")
def get_history(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    entries = load_user_history(user["username"])
    # Return lightweight entries (no full result blob) for the list view
    return [
        {
            "id": e.get("id"),
            "analyzed_at": e.get("analyzed_at"),
            "repo_url": e.get("repo_url"),
            "repo_name": e.get("repo_name"),
            "overall_quality_score": e.get("overall_quality_score"),
            "recommendation": e.get("recommendation"),
            "summary": e.get("summary"),
            "had_resume": e.get("had_resume"),
            "had_jd": e.get("had_jd"),
        }
        for e in entries
    ]


@app.get("/api/history/{analysis_id}")
def get_history_entry(
    analysis_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    entries = load_user_history(user["username"])
    for entry in entries:
        if entry.get("id") == analysis_id:
            return entry
    raise HTTPException(status_code=404, detail="Analysis not found")


# ---------------------------------------------------------------------------
# Chat / RAG
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    job_id: str | None = None
    analysis_id: str | None = None
    message: str
    history: list[ChatMessage] = []


@app.post("/api/chat")
def chat(
    req: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    import httpx

    # Resolve result from job store or history
    result: dict[str, Any] | None = None
    if req.job_id:
        job = job_store.get(req.job_id)
        if job and job.username == user["username"] and job.status == "done":
            result = job.result
    if result is None and req.analysis_id:
        entries = load_user_history(user["username"])
        for e in entries:
            if e.get("id") == req.analysis_id:
                result = e.get("result")
                break

    if result is None:
        raise HTTPException(status_code=404, detail="Analysis result not found")

    verdict = result.get("verdict", {})
    analysis_data = result.get("analysis_data", {})

    import json
    context_blob = json.dumps({
        "verdict": verdict,
        "repo_metadata": analysis_data.get("repo_metadata"),
        "commit_patterns": analysis_data.get("commit_patterns"),
        "job_description": result.get("job_description"),
        "resume_data": result.get("resume_data"),
        "skill_matches": result.get("skill_matches"),
    }, default=str)[:12000]

    system_prompt = (
        "You are CodeLens Assistant, an expert code reviewer helping a recruiter understand a candidate's analysis. "
        "Answer questions concisely based on the analysis data provided. "
        "If asked to generate a chart, respond with a JSON block like: "
        '[CHART]{"type":"bar","labels":[...],"data":[...]}[/CHART]\n\n'
        f"Analysis data:\n{context_blob}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in req.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.message})

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    model = os.getenv("OPENROUTER_MODEL", "openrouter/anthropic/claude-haiku-4-5")

    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "max_tokens": 1024},
        timeout=60,
    )
    resp.raise_for_status()
    reply = resp.json()["choices"][0]["message"]["content"]
    return {"response": reply}


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


@app.post("/api/payment/checkout")
def create_checkout(
    github_url: str = Form(""),
    job_description: str = Form(""),
    company_github_url: str = Form(""),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    from tools.payments import save_pending_analysis

    username = user["username"]
    try:
        save_pending_analysis(username, github_url, job_description, company_github_url)
        checkout_url = create_checkout_session(username)
        return {"checkout_url": checkout_url}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/payment/confirm")
def confirm_payment(
    gh_user: str = Form(...),
    sid: str = Form(...),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if gh_user != user["username"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        added = process_successful_payment(gh_user, sid)
        db_user = get_user(gh_user) or {}
        return {
            "credit_added": added,
            "free_uses_remaining": int(db_user.get("free_uses_remaining", 0)),
            "paid_uses_remaining": int(db_user.get("paid_uses_remaining", 0)),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Periodic cleanup (runs in background thread)
# ---------------------------------------------------------------------------

def _cleanup_loop() -> None:
    import time
    while True:
        time.sleep(1800)
        job_store.purge_old(max_age_seconds=3600)


threading.Thread(target=_cleanup_loop, daemon=True).start()
