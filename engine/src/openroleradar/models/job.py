from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from openroleradar.models.enums import (
    AcademicTerm,
    CareerLevel,
    CompensationPeriod,
    EligibilityMatch,
    EmploymentType,
    EvidenceStatus,
    JobLifecycle,
    RemoteScope,
    WorkplaceType,
)


class EvidenceClaim(BaseModel):
    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_rule: str | None = None
    evidence_excerpt: str | None = Field(default=None, max_length=500)


class MobilityBenefits(BaseModel):
    visa_sponsorship: EvidenceClaim = Field(default_factory=EvidenceClaim)
    immigration_assistance: EvidenceClaim = Field(default_factory=EvidenceClaim)
    international_candidates: EvidenceClaim = Field(default_factory=EvidenceClaim)
    relocation_assistance: EvidenceClaim = Field(default_factory=EvidenceClaim)
    relocation_stipend: EvidenceClaim = Field(default_factory=EvidenceClaim)
    moving_expenses: EvidenceClaim = Field(default_factory=EvidenceClaim)
    airfare: EvidenceClaim = Field(default_factory=EvidenceClaim)
    travel_reimbursement: EvidenceClaim = Field(default_factory=EvidenceClaim)
    housing_provided: EvidenceClaim = Field(default_factory=EvidenceClaim)
    housing_stipend: EvidenceClaim = Field(default_factory=EvidenceClaim)
    temporary_housing: EvidenceClaim = Field(default_factory=EvidenceClaim)
    fully_funded_relocation: EvidenceClaim = Field(default_factory=EvidenceClaim)


class Location(BaseModel):
    raw: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    country_code: str | None = Field(default=None, max_length=2)
    continent: str | None = None


class Compensation(BaseModel):
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    period: CompensationPeriod = CompensationPeriod.UNKNOWN
    raw: str | None = None


class EducationRequirements(BaseModel):
    student_required: bool | None = None
    return_to_school_required: bool | None = None
    degree_levels: list[str] = Field(default_factory=list)
    graduation_years: list[int] = Field(default_factory=list)
    majors: list[str] = Field(default_factory=list)
    academic_years: list[str] = Field(default_factory=list)


class Eligibility(BaseModel):
    work_authorization: str | None = None
    explicit_allowed_countries: list[str] = Field(default_factory=list)
    explicit_excluded_countries: list[str] = Field(default_factory=list)
    citizenship_requirements: list[str] = Field(default_factory=list)
    residency_requirements: list[str] = Field(default_factory=list)
    security_clearance: str | None = None
    export_control_restrictions: bool | None = None
    language_requirements: list[str] = Field(default_factory=list)
    origin_match: EligibilityMatch = EligibilityMatch.UNKNOWN


class DisciplineClassification(BaseModel):
    primary: str = "other"
    secondary: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Provenance(BaseModel):
    adapter: str
    source_id: str
    source_job_id: str
    fetched_at: datetime
    source_posted_at: datetime | None = None
    first_seen_at: datetime | None = None
    parser_version: str = "1.0.0"
    classification_version: str = "1.0.0"
    content_hash: str
    source_health_at_fetch: str = "healthy"
    first_party_verified: bool = False


class Job(BaseModel):
    job_id: str
    source_job_id: str
    requisition_id: str | None = None
    company_id: str
    company_name: str
    title: str
    job_url: str
    apply_url: str
    careers_url: str | None = None
    source_url: str | None = None
    summary: str | None = Field(default=None, max_length=2000)
    career_level: CareerLevel = CareerLevel.UNKNOWN
    career_level_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    disciplines: DisciplineClassification = Field(default_factory=DisciplineClassification)
    skills: list[str] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    workplace_type: WorkplaceType = WorkplaceType.UNKNOWN
    remote_scope: RemoteScope = RemoteScope.UNKNOWN
    remote_allowed_countries: list[str] = Field(default_factory=list)
    remote_allowed_regions: list[str] = Field(default_factory=list)
    source_posted_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime | None = None
    closed_at: datetime | None = None
    reopened_at: datetime | None = None
    application_deadline: datetime | None = None
    expected_start_date: datetime | None = None
    expected_end_date: datetime | None = None
    duration_months: int | None = None
    academic_term: AcademicTerm = AcademicTerm.UNKNOWN
    compensation: Compensation | None = None
    education: EducationRequirements = Field(default_factory=EducationRequirements)
    eligibility: Eligibility = Field(default_factory=Eligibility)
    mobility: MobilityBenefits = Field(default_factory=MobilityBenefits)
    provenance: Provenance
    lifecycle: JobLifecycle = JobLifecycle.OPEN
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Company(BaseModel):
    company_id: str
    name: str
    normalized_name: str
    canonical_domain: str
    aliases: list[str] = Field(default_factory=list)
    careers_urls: list[str] = Field(default_factory=list)
    ats_sources: list[str] = Field(default_factory=list)
    headquarters: Location | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    active_job_count: int = 0
    historical_job_count: int = 0


class Source(BaseModel):
    source_id: str
    company_id: str
    company_name: str
    company_domain: str
    careers_url: str
    adapter: str
    adapter_tenant: str
    discovered_via: str = "seed"
    discovery_confidence: float = 1.0
    first_discovered_at: datetime
    last_validated_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    poll_tier: str = "warm"
    next_due_at: datetime | None = None
    health_status: str = "healthy"
    etag: str | None = None
    last_modified: str | None = None
    enabled: bool = True
    last_response_hash: str | None = None
    # Yield scoring — boards that never emit public early-career roles demote.
    public_open_count: int = 0
    last_public_seen_at: datetime | None = None
    zero_yield_streak: int = 0
    successful_polls: int = 0


class JobEvent(BaseModel):
    event_type: str
    job_id: str
    timestamp: datetime
    changed_fields: list[str] = Field(default_factory=list)
    previous_hash: str | None = None
    new_hash: str | None = None


class RawJob(BaseModel):
    source_job_id: str
    requisition_id: str | None = None
    title: str
    job_url: str
    apply_url: str
    locations_raw: list[str] = Field(default_factory=list)
    description_text: str | None = None
    summary: str | None = None
    employment_type: str | None = None
    department: str | None = None
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    application_deadline: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
