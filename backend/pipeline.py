"""
Analysis pipeline — pure Python, no Streamlit dependencies.
Extracted from app.py so FastAPI can run it in background threads.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.crew import CodeLensCrew
from guardrails.output_filter import OutputFilter
from rag.indexer import CodeIndexer
from rag.retriever import CodeRetriever
from tools.github_api import GithubAnalyzer
from tools.gitnexus_tool import open_repo_analyzer
from tools.pinecone_tool import PineconeStore
from tools.project_env import load_project_env
from tools.resume_parser import ResumeParser, SkillMatcher

load_project_env()

HISTORY_DIR = _ROOT / "data" / "history"
RESUMES_DIR = _ROOT / "data" / "resumes"

# ---------------------------------------------------------------------------
# Simple TTL cache (replaces @st.cache_data)
# ---------------------------------------------------------------------------
_github_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 1800


def _cache_get(key: str) -> Any | None:
    entry = _github_cache.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _github_cache[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def fetch_github_analysis(github_url: str) -> dict[str, Any]:
    cached = _cache_get(github_url)
    if cached is not None:
        return cached
    analyzer = GithubAnalyzer(github_url)
    result: dict[str, Any] = {
        "repo_metadata": analyzer.get_repo_metadata(),
        "commits": analyzer.get_commits(),
        "commit_patterns": analyzer.get_commit_patterns(),
    }
    _cache_set(github_url, result)
    return result


def fetch_company_style_summary(company_github_url: str) -> str:
    key = f"company:{company_github_url}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    analyzer = GithubAnalyzer(company_github_url)
    metadata = analyzer.get_repo_metadata()
    patterns = analyzer.get_commit_patterns()
    result = (
        f"Company repo {metadata.get('name', '')} uses primary language "
        f"{metadata.get('primary_language', 'unknown')}, default branch "
        f"{metadata.get('default_branch_name', 'unknown')}, average diff size "
        f"{patterns.get('avg_diff_size', 0)}, commit message average length "
        f"{patterns.get('message_avg_length', 0)}, and single_branch="
        f"{patterns.get('single_branch', False)}."
    )
    _cache_set(key, result)
    return result


def candidate_username_from_url(github_url: str) -> str:
    parsed = urlparse(github_url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    return parts[0] if parts else "candidate"


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------

class _ResumeProxy:
    """Wraps raw bytes so resume parsing code works without Streamlit's UploadedFile."""
    def __init__(self, filename: str, data: bytes) -> None:
        self.name = filename
        self._data = data

    def getvalue(self) -> bytes:
        return self._data

    def read(self) -> bytes:
        return self._data


def read_uploaded_resume(
    resume_bytes: bytes, resume_filename: str
) -> tuple[dict[str, Any] | None, str | None]:
    parser = ResumeParser()
    suffix = Path(resume_filename).suffix.lower()
    if suffix == ".pdf":
        resume_data = parser.parse_from_pdf(resume_bytes)
        resume_text = _build_resume_text(resume_data)
        return resume_data, resume_text
    resume_text = resume_bytes.decode("utf-8", errors="replace")
    resume_data = parser.parse_from_text(resume_text)
    return resume_data, resume_text


def _build_resume_text(resume_data: dict[str, Any]) -> str:
    lines = [
        "Skills: " + ", ".join(resume_data.get("skills", [])),
        f"Experience level: {resume_data.get('experience_level', '')}",
        f"Years experience: {resume_data.get('years_experience', 0)}",
    ]
    for project in resume_data.get("projects", []):
        lines.append(
            " | ".join([
                project.get("name", ""),
                project.get("description", ""),
                ", ".join(project.get("technologies", [])),
                ", ".join(project.get("claimed_features", [])),
            ])
        )
    return "\n".join(line for line in lines if line.strip())


def build_code_sample(files: list[dict[str, Any]], max_chars: int = 6000) -> str:
    samples: list[str] = []
    total = 0
    for file_data in files:
        text = file_data.get("content") or file_data.get("text") or ""
        if not text:
            symbols = file_data.get("symbols") or file_data.get("parsed_symbols") or []
            if isinstance(symbols, dict):
                symbols = symbols.get("items", [])
            snippets = [
                str(s.get("code") or s.get("text") or s.get("content") or "")
                for s in symbols[:5]
                if isinstance(s, dict)
            ]
            text = "\n\n".join(s for s in snippets if s)
        if not text:
            continue
        chunk = f"# File: {file_data.get('file_path') or file_data.get('path') or 'unknown'}\n{text}"
        remaining = max_chars - total
        if remaining <= 0:
            break
        samples.append(chunk[:remaining])
        total += len(chunk)
    return "\n\n---\n\n".join(samples)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_analysis_pipeline(
    github_url: str,
    resume_bytes: bytes | None,
    resume_filename: str | None,
    job_description: str,
    company_github_url: str,
    on_progress: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """
    Run the full CodeLens analysis pipeline.
    on_progress(msg) is called at each stage so callers can update a job store.
    """
    output_filter = OutputFilter()
    candidate_username = candidate_username_from_url(github_url)

    on_progress("Validating repository...")
    output_filter.validate_repo_url(github_url)
    if company_github_url:
        output_filter.validate_repo_url(company_github_url)

    github_result = fetch_github_analysis(github_url)
    repo_metadata = github_result["repo_metadata"]
    commits = github_result["commits"]
    commit_patterns = github_result["commit_patterns"]
    on_progress(f"Fetching commit history ({len(commits)} commits found)...")

    on_progress("Indexing codebase with GitNexus...")

    def _repo_phase(msg: str) -> None:
        on_progress(msg)

    with open_repo_analyzer(github_url, on_phase=_repo_phase) as gitnexus:
        files = gitnexus.get_file_contents()
        knowledge_graph = gitnexus.get_knowledge_graph()
        output_filter.validate_repo_size(files, commits)
        output_filter.set_analysis_context(commits, files)

        on_progress("Building vector embeddings...")
        store = PineconeStore()
        indexer = CodeIndexer()
        index_count = indexer.index_repo_files(files, candidate_username, store)
        retriever = CodeRetriever()
        baseline_comparison = retriever.get_baseline_comparison(
            build_code_sample(files), store
        )

    resume_data = None
    resume_text = None
    skill_matches = None
    project_matches = None
    undeclared_skills: list[str] = []

    if resume_bytes and resume_filename:
        on_progress("Parsing resume...")
        resume_data, resume_text = read_uploaded_resume(resume_bytes, resume_filename)
        if resume_data and resume_text:
            indexer.index_resume(resume_data, resume_text, candidate_username, store)
            matcher = SkillMatcher(store)
            skill_matches = matcher.match_skills_to_code(
                resume_data.get("skills", []),
                f"candidate-{candidate_username}",
            )
            project_matches = [
                matcher.match_project_claims(project, f"candidate-{candidate_username}")
                for project in resume_data.get("projects", [])
            ]
            undeclared_skills = matcher.find_undeclared_skills(
                resume_data.get("skills", []),
                f"candidate-{candidate_username}",
            )

    parsed_job_description = None
    if job_description.strip():
        parsed_job_description = ResumeParser().parse_job_description(job_description.strip())

    company_style_summary = None
    if company_github_url.strip():
        company_style_summary = fetch_company_style_summary(company_github_url.strip())

    analysis_data: dict[str, Any] = {
        "repo_metadata": repo_metadata,
        "commits": commits,
        "commit_patterns": commit_patterns,
        "files": files,
        "knowledge_graph": knowledge_graph,
        "baseline_comparison": baseline_comparison,
        "resume_data": resume_data,
        "skill_matches": skill_matches,
        "project_matches": project_matches,
        "job_description": parsed_job_description,
        "company_style_summary": company_style_summary,
    }

    on_progress("Running agent analysis...")
    crew_result = CodeLensCrew(analysis_data).run_with_reports()
    verdict = output_filter.filter_verdict(crew_result["verdict"])
    verdict["vibe_coding_flags"] = output_filter.flag_vibe_coding(files, commit_patterns)

    on_progress("Finalizing verdict...")

    return {
        "verdict": verdict,
        "reports": crew_result.get("reports", {}),
        "analysis_data": analysis_data,
        "resume_data": resume_data,
        "resume_text": resume_text,
        "skill_matches": skill_matches or [],
        "project_matches": project_matches or [],
        "undeclared_skills": undeclared_skills,
        "job_description": parsed_job_description,
        "company_style_summary": company_style_summary,
        "indexed_chunks": index_count,
        "candidate_namespace": f"candidate-{candidate_username}",
    }


# ---------------------------------------------------------------------------
# History helpers (username passed explicitly — no session state)
# ---------------------------------------------------------------------------

def _history_path(username: str) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR / f"{username}.json"


def load_user_history(username: str) -> list[dict[str, Any]]:
    path = _history_path(username)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        analyses = payload.get("analyses", [])
        return analyses if isinstance(analyses, list) else []
    except Exception:
        return []


def _save_user_history(username: str, analyses: list[dict[str, Any]]) -> None:
    path = _history_path(username)
    path.write_text(json.dumps({"analyses": analyses}, indent=2), encoding="utf-8")


def save_analysis_to_history(
    username: str,
    result: dict[str, Any],
    github_url: str,
    had_resume: bool,
    had_jd: bool,
    resume_bytes: bytes | None = None,
    resume_filename: str | None = None,
    job_description: str = "",
) -> str:
    """Save result to user history. Returns the analysis_id."""
    verdict = result["verdict"]
    repo_metadata = result["analysis_data"].get("repo_metadata", {})
    analyses = load_user_history(username)

    analysis_id = str(uuid.uuid4())
    user_resumes_dir = RESUMES_DIR / username
    user_resumes_dir.mkdir(parents=True, exist_ok=True)

    resume_path: str | None = None
    if resume_bytes and resume_filename:
        suffix = Path(resume_filename).suffix or ".pdf"
        pdf_file = user_resumes_dir / f"{analysis_id}{suffix}"
        pdf_file.write_bytes(resume_bytes)
        resume_path = str(pdf_file)

    jd_path: str | None = None
    if job_description.strip():
        jd_file = user_resumes_dir / f"{analysis_id}_jd.txt"
        jd_file.write_text(job_description, encoding="utf-8")
        jd_path = str(jd_file)

    entry: dict[str, Any] = {
        "id": analysis_id,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "repo_url": github_url,
        "repo_name": (
            repo_metadata.get("name")
            or repo_metadata.get("full_name")
            or github_url
        ),
        "overall_quality_score": verdict.get("overall_quality_score"),
        "ai_usage_score": verdict.get("ai_usage_score"),
        "commit_health_score": verdict.get("commit_health_score"),
        "resume_match_score": verdict.get("resume_match_score"),
        "recommendation": verdict.get("recommendation"),
        "summary": verdict.get("summary"),
        "had_resume": had_resume,
        "had_jd": had_jd,
        "resume_path": resume_path,
        "jd_path": jd_path,
        "result": result,
    }
    analyses.insert(0, entry)
    _save_user_history(username, analyses)
    return analysis_id
