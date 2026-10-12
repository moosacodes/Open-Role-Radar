"""Shared helpers for deciding which jobs appear in public exports."""

from __future__ import annotations

import re
from functools import lru_cache

from openroleradar.config import load_project_config
from openroleradar.models.enums import CareerLevel, JobLifecycle
from openroleradar.models.job import Job
from openroleradar.normalize.text import canonicalize_text

EARLY_CAREER_LEVELS: frozenset[CareerLevel] = frozenset(
    {
        CareerLevel.INTERNSHIP,
        CareerLevel.CO_OP,
        CareerLevel.NEW_GRAD,
        CareerLevel.ENTRY_LEVEL,
        CareerLevel.APPRENTICESHIP,
        CareerLevel.GRADUATE_PROGRAM,
        CareerLevel.ROTATIONAL_PROGRAM,
        CareerLevel.RESEARCH_INTERNSHIP,
        CareerLevel.FELLOWSHIP,
        CareerLevel.STUDENT_PROGRAM,
    }
)

# CS / software-engineering oriented tracks for student-facing public feed.
CS_DISCIPLINES: frozenset[str] = frozenset(
    {
        "software",
        "frontend",
        "backend",
        "full_stack",
        "mobile",
        "systems",
        "infrastructure",
        "cloud",
        "devops",
        "sre",
        "cybersecurity",
        "networking",
        "data_engineering",
        "data_science",
        "machine_learning",
        "artificial_intelligence",
        "quantitative_development",
        "quantitative_research",
        "hardware",
        "embedded",
        "firmware",
        "robotics",
        "qa_automation",
        "product",
    }
)

_CS_TITLE_RE = re.compile(
    r"\b("
    r"software|engineer|developer|swe|sde|programmer|full[\s-]?stack|"
    r"front[\s-]?end|back[\s-]?end|mobile|ios|android|data|machine\s+learning|"
    r"\bml\b|\bai\b|artificial\s+intelligence|cyber|security|devops|sre|"
    r"platform|cloud|systems|infrastructure|quant(?:itative)?|trading|"
    r"robotics|firmware|embedded|computer\s+science|informatics|"
    r"site\s+reliability|research\s+intern"
    r")\b",
    re.I,
)

_NOISE_TITLE_RE = re.compile(
    r"\b("
    r"technical support|support engineer|help[\s-]?desk|customer support|"
    r"business enablement|guest experience|team member|sales associate|"
    r"store associate|barista|cashier|retail"
    r")\b",
    re.I,
)

_CLOSED_APPLICATION_RE = re.compile(
    r"("
    r"no longer accepting(?: applications)?"
    r"|applications? (?:are|have been) closed"
    r"|position (?:has been|is) filled"
    r"|this (?:role|position|job) (?:has been|is) closed"
    r"|not (?:currently )?accepting applications"
    r"|hiring for this role is closed"
    r")"
)


@lru_cache(maxsize=1)
def _configured_min_confidence() -> float:
    try:
        config = load_project_config()
        value = float(config.classification.get("early_career_min_confidence", 0.55))
    except Exception:
        value = 0.55
    return max(0.0, min(value, 1.0))


def _applications_open(job: Job) -> bool:
    """Reject roles that look closed even if lifecycle has not flipped yet."""
    if not (job.apply_url and job.job_url):
        return False
    blob = canonicalize_text(f"{job.title} {job.summary or ''}")
    return not bool(_CLOSED_APPLICATION_RE.search(blob))


def is_cs_relevant(job: Job) -> bool:
    """Prefer software/CS early-career roles over random retail/ops listings."""
    title = job.title or ""
    if _NOISE_TITLE_RE.search(title):
        return False
    primary = (job.disciplines.primary or "").lower()
    if primary in CS_DISCIPLINES:
        return True
    return bool(_CS_TITLE_RE.search(title))


def is_early_career_job(job: Job, *, min_confidence: float | None = None) -> bool:
    """Open early-career role with a usable apply link (discipline-agnostic)."""
    threshold = _configured_min_confidence() if min_confidence is None else min_confidence
    if job.lifecycle not in {JobLifecycle.OPEN, JobLifecycle.REOPENED}:
        return False
    if not _applications_open(job):
        return False
    if job.career_level not in EARLY_CAREER_LEVELS:
        return False
    if job.career_level_confidence < threshold:
        return False
    return True


def is_public_job(
    job: Job,
    *,
    min_confidence: float | None = None,
    mode: str = "strict",
) -> bool:
    """Return True when a job should appear on the public site/API/feeds.

    Modes:
    - strict (default): CS-relevant early-career only (main site)
    - wide: any early-career discipline (secondary feed)
    """
    if not is_early_career_job(job, min_confidence=min_confidence):
        return False
    if mode == "wide":
        return not bool(_NOISE_TITLE_RE.search(job.title or ""))
    return is_cs_relevant(job)
