"""Re-apply taxonomy classification to stored jobs before public export."""

from __future__ import annotations

from pathlib import Path

from openroleradar.classify.academic_term import classify_academic_term
from openroleradar.classify.career_level import classify_career_level
from openroleradar.classify.discipline import classify_discipline
from openroleradar.config import load_project_config
from openroleradar.models.enums import CareerLevel, JobLifecycle
from openroleradar.models.state import LiveState
from openroleradar.normalize.text import html_to_plaintext, truncate_summary


def reclassify_state(
    state: LiveState,
    *,
    root: Path | None = None,
    open_only: bool = True,
    force: bool = False,
) -> int:
    """Refresh career level, academic term, and sanitize summaries before export.

    Returns the number of jobs whose career_level or confidence changed.
    By default only open/reopened roles are touched, and jobs already stamped
    with the current classification version are skipped (summary sanitize only).
    """
    config = load_project_config(root) if root is not None else load_project_config()
    current_version = str(config.versions.classification)
    changed = 0
    skipped = 0

    for job in state.jobs.values():
        if open_only and job.lifecycle not in {JobLifecycle.OPEN, JobLifecycle.REOPENED}:
            continue

        cleaned_summary = truncate_summary(html_to_plaintext(job.summary))
        if cleaned_summary != job.summary:
            job.summary = cleaned_summary

        already_current = (
            not force
            and job.provenance.classification_version == current_version
            and job.career_level != CareerLevel.UNKNOWN
            and job.career_level_confidence > 0
        )
        if already_current:
            skipped += 1
            continue

        level, confidence = classify_career_level(
            job.title,
            job.summary,
            employment_type=job.employment_type.value if job.employment_type else None,
            metadata=job.extra,
            root=root,
        )
        disciplines = classify_discipline(job.title, description=job.summary, root=root)
        academic_term = classify_academic_term(job.title, job.summary, root=root)
        if job.career_level != level or abs(job.career_level_confidence - confidence) > 1e-9:
            changed += 1
        job.career_level = level
        job.career_level_confidence = confidence
        job.disciplines = disciplines
        job.academic_term = academic_term
        job.provenance.classification_version = current_version

    state.metadata["classification_skipped"] = skipped
    state.metadata["classification_version"] = current_version
    return changed
