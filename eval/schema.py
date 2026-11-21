"""
Pydantic schemas and models for Prawdowid Evaluation Dataset.
Covers benchmark test cases, deterministic invariants, qualitative rubrics,
and runtime execution traces.
"""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict


class OriginEnum(str, Enum):
    organizer_seed = "organizer_seed"
    extended_real = "extended_real"


class SystemStatusEnum(str, Enum):
    VERDICT_RENDERED = "VERDICT_RENDERED"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"


class VerdictEnum(str, Enum):
    POTWIERDZONE = "POTWIERDZONE"
    FALSZYWE = "FALSZYWE"
    WPROWADZAJACE_W_BLAD = "WPROWADZAJACE_W_BLAD"
    BRAK_WYSTARCZAJACYCH_DOWODOW = "BRAK_WYSTARCZAJACYCH_DOWODOW"
    NIE_PODLEGA_WERYFIKACJI = "NIE_PODLEGA_WERYFIKACJI"


class TicketActionEnum(str, Enum):
    NONE = "NONE"
    CREATE = "CREATE"
    DEDUPLICATE_EXISTING = "DEDUPLICATE_EXISTING"


class ChannelEnum(str, Enum):
    web = "web"
    secondary_channel = "secondary_channel"


class TestCaseInput(BaseModel):
    __test__ = False
    query: str = Field(..., description="Query / claim entered by user")
    attachment_url: Optional[str] = Field(default=None, description="Optional URL entered by user")
    attachment_image_path: Optional[str] = Field(
        default=None, description="Path to image fixture relative to repo root"
    )

    model_config = ConfigDict(extra="forbid")


class EnvironmentOverrides(BaseModel):
    simulate_api_failure: Optional[str] = Field(
        default=None,
        description="Simulated API error identifier, e.g. 'google_factcheck_api_503'",
    )
    mock_untrusted_url_fixture: Optional[str] = Field(
        default=None,
        description="Path to mock HTML page fixture relative to repo root",
    )
    mock_trusted_api_fixture: Optional[str] = Field(
        default=None,
        description="Path to deterministic snapshot fixture for a trusted/official live API feed (e.g. RSO XML) relative to repo root",
    )

    model_config = ConfigDict(extra="forbid")


class DeterministicInvariants(BaseModel):
    expected_system_status: SystemStatusEnum = Field(
        default=SystemStatusEnum.VERDICT_RENDERED,
        description="Expected top-level outcome: VERDICT_RENDERED, TECHNICAL_ERROR, CLARIFICATION_NEEDED",
    )
    primary_verdict: Optional[VerdictEnum] = Field(
        default=None,
        description="Strict expected verdict (null when TECHNICAL_ERROR)",
    )
    acceptable_verdicts: list[VerdictEnum] = Field(
        default_factory=list,
        description="List of acceptable verdicts for relaxed macro-F1 evaluation",
    )
    abstention_expected: bool = Field(
        default=False,
        description="True if system must abstain (BRAK_WYSTARCZAJACYCH_DOWODOW or NIE_PODLEGA_WERYFIKACJI)",
    )
    min_citations_count: int = Field(
        default=1,
        description="Minimum number of verifiable citations required",
    )
    required_domains_any_of: list[str] = Field(
        default_factory=list,
        description="At least one of these domains must be cited",
    )
    required_urls_any_of: list[str] = Field(
        default_factory=list,
        description="At least one of these exact URLs must be cited",
    )
    requires_live_url_verification: bool = Field(
        default=False,
        description="True if primary source availability check (HEAD/GET live) is required",
    )
    requires_1hop_link: bool = Field(
        default=False,
        description="True if bot must follow/reference 1-hop outbound link from official source",
    )
    expected_security_incident_logged: bool = Field(
        default=False,
        description="True if prompt injection attempt must be detected and logged",
    )
    expected_fimi_warning_only: bool = Field(
        default=False,
        description="True if FIMI warning must be displayed without determining verdict solely on domain",
    )
    expected_moderation_ticket_action: TicketActionEnum = Field(
        default=TicketActionEnum.NONE,
        description="Expected moderation loop action: NONE, CREATE, DEDUPLICATE_EXISTING",
    )

    model_config = ConfigDict(extra="forbid")


class LLMJudgeRubric(BaseModel):
    reference_answer: str = Field(..., description="Gold reference answer")
    must_include_claims: list[str] = Field(
        default_factory=list,
        description="Key factual claims the answer must include",
    )
    must_not_include_claims: list[str] = Field(
        default_factory=list,
        description="Prohibited assertions or hallucinations",
    )
    scenario_specific_criteria: Optional[str] = Field(
        default=None,
        description="Specific instructions for LLM judge evaluating this scenario",
    )

    model_config = ConfigDict(extra="forbid")


class TestCase(BaseModel):
    __test__ = False
    id: str = Field(..., description="Unique test case identifier (e.g. TC-A1-01)")
    scenario: str = Field(..., description="Scenario code: A1, A2, B, C, D, E, F, G, H, I, J")
    scenario_name: str = Field(..., description="Human readable scenario title")
    origin: OriginEnum = Field(..., description="organizer_seed or extended_real")
    subsystems_tested: list[str] = Field(
        default_factory=list, description="Subsystems exercised by this test case"
    )
    channels: list[ChannelEnum] = Field(
        default_factory=lambda: [ChannelEnum.web],
        description="Channels to test (['web'] or ['web', 'secondary_channel'])",
    )
    session_group_id: Optional[str] = Field(
        default=None,
        description="Group ID for deduplication pairs (e.g. GRP-PCIM-DIESEL)",
    )
    input: TestCaseInput
    environment_overrides: EnvironmentOverrides = Field(default_factory=EnvironmentOverrides)
    deterministic_invariants: DeterministicInvariants
    llm_judge_rubric: LLMJudgeRubric

    model_config = ConfigDict(extra="forbid")


# --- Runtime / Execution Models ---

class EvidenceChunk(BaseModel):
    id: str = Field(..., description="Source ID (e.g. S1)")
    source_type: str = Field(..., description="official | factcheck | fimi_catalog | user_url | law | telemetry")
    retrieval_method: str = Field(..., description="rag | live_api | web_search_whitelist | user_fetch")
    publisher: str
    domain: str
    url: str
    title: str
    published_at: Optional[str] = None
    fetched_at: Optional[str] = None
    content: str
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    source_id: str
    publisher: str = Field(..., description="Publisher name (required by MSZ Section 2)")
    url: str
    domain: str
    title: str
    published_at: Optional[str] = Field(
        default=None,
        description="Publication date/timestamp (ISO 8601, required by MSZ Section 2 when available)",
    )
    quoted_snippet: Optional[str] = None
    verified_live_at: Optional[str] = None
    is_1hop_link: bool = False


class SystemExecutionTrace(BaseModel):
    test_case_id: str
    channel: ChannelEnum = ChannelEnum.web
    system_status: SystemStatusEnum
    rendered_verdict: Optional[VerdictEnum] = None
    answer_text: str
    citations: list[Citation] = Field(default_factory=list)
    retrieved_evidence: list[EvidenceChunk] = Field(default_factory=list)
    latency_sec: float = 0.0
    estimated_cost_usd: float = 0.0
    prompt_injection_detected: bool = False
    fimi_warning_displayed: bool = False
    moderation_ticket_created: bool = False
    moderation_ticket_id: Optional[str] = None
    moderation_ticket_group_id: Optional[str] = None
    error_message: Optional[str] = None
