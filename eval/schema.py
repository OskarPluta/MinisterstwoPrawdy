"""
Pydantic schemas and models for Prawdowid Evaluation Dataset.
Covers benchmark test cases, deterministic invariants, qualitative rubrics,
and runtime execution traces.
"""

from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field, ConfigDict, model_validator


class OriginEnum(str, Enum):
    organizer_seed = "organizer_seed"
    extended_real = "extended_real"


class SystemStatusEnum(str, Enum):
    VERDICT_RENDERED = "VERDICT_RENDERED"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"  # request is not a fact-check (advice, writing a text, etc.)
    UNABLE_TO_VERIFY = "UNABLE_TO_VERIFY"  # source unreachable without bypassing protections (paywall, login wall)


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


ABSTAIN_VERDICTS = {"BRAK_WYSTARCZAJACYCH_DOWODOW", "NIE_PODLEGA_WERYFIKACJI"}
_LOCAL_HOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0")


def _host_matches(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


class ClaimExpectation(BaseModel):
    """Expected verdict for one claim in a multi-claim query."""

    claim: str = Field(..., description="Canonical wording of the single claim")
    primary_verdict: "VerdictEnum"
    acceptable_verdicts: list["VerdictEnum"] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class TestCaseInput(BaseModel):
    __test__ = False
    query: str = Field(..., description="Query / claim entered by user")
    attachment_url: Optional[str] = Field(
        default=None,
        description="Optional URL entered by user. Must look like a real public URL (neutral slug); "
        "the runner maps it to a local file via environment_overrides.mock_untrusted_url_fixture",
    )
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
        description="Path to mock HTML page fixture relative to repo root; served in place of input.attachment_url "
        "(and any URL in input.query) so the system never sees the local path",
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
        description="True iff primary_verdict belongs to ABSTAIN_VERDICTS (BRAK_WYSTARCZAJACYCH_DOWODOW / NIE_PODLEGA_WERYFIKACJI)",
    )
    verdict_subject_claim: Optional[str] = Field(
        default=None,
        description="The base claim the verdict refers to when the query is a meta-question "
        "(e.g. 'Did Demagog check X?' -> verdict is about X, not about whether Demagog checked it)",
    )
    expected_claims: list[ClaimExpectation] = Field(
        default_factory=list,
        description="Per-claim expected verdicts for multi-claim queries (empty for single-claim cases)",
    )
    min_citations_count: int = Field(
        default=1,
        description="Minimum number of verifiable citations required",
    )
    required_domains_any_of: list[str] = Field(
        default_factory=list,
        description="At least one of these domains must be cited. Every listed domain must have at least one "
        "matching URL in required_urls_any_of (when that list is non-empty)",
    )
    required_domains_all_of: list[str] = Field(
        default_factory=list,
        description="Each of these domains must be cited (independent-source requirement, e.g. FIMI context + substantive rebuttal)",
    )
    required_urls_any_of: list[str] = Field(
        default_factory=list,
        description="At least one of these exact URLs must be cited. Prefer human-readable pages over raw API endpoints; "
        "list equivalent HTML and API URLs together when both are acceptable",
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
    expected_fimi_context_warning: bool = Field(
        default=False,
        description="True if a FIMI context warning must be displayed; the verdict itself must never rest solely on "
        "the domain or the incident catalogue",
    )
    pii_must_be_masked: list[str] = Field(
        default_factory=list,
        description="Literal personal-data substrings from the query that must not appear in the persisted trace, "
        "logs or moderation ticket (SystemExecutionTrace.persisted_query and related fields)",
    )
    expected_moderation_ticket_action: TicketActionEnum = Field(
        default=TicketActionEnum.NONE,
        description="Expected moderation loop action: NONE, CREATE, DEDUPLICATE_EXISTING",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_consistency(self) -> "DeterministicInvariants":
        if self.expected_system_status == SystemStatusEnum.VERDICT_RENDERED:
            if self.primary_verdict is None:
                raise ValueError("primary_verdict must not be null when expected_system_status is VERDICT_RENDERED")
        else:
            if self.acceptable_verdicts:
                raise ValueError("acceptable_verdicts must be empty unless expected_system_status is VERDICT_RENDERED")
            if self.abstention_expected:
                raise ValueError("abstention_expected must be False unless expected_system_status is VERDICT_RENDERED")
        if self.primary_verdict is not None:
            if self.acceptable_verdicts and self.primary_verdict not in self.acceptable_verdicts:
                raise ValueError("primary_verdict must be included in acceptable_verdicts")
            must_abstain = self.primary_verdict.value in ABSTAIN_VERDICTS
            if self.abstention_expected != must_abstain:
                raise ValueError(
                    "abstention_expected must be True iff primary_verdict is an abstention verdict"
                )
        if self.required_urls_any_of:
            domains = [*self.required_domains_any_of, *self.required_domains_all_of]
            for domain in domains:
                if not any(_host_matches(u, domain) for u in self.required_urls_any_of):
                    raise ValueError(f"domain {domain!r} has no matching URL in required_urls_any_of")
            if domains:
                for u in self.required_urls_any_of:
                    if not any(_host_matches(u, d) for d in domains):
                        raise ValueError(
                            f"url {u!r} does not match any domain in required_domains_any_of/required_domains_all_of"
                        )
        if len(self.required_domains_all_of) > self.min_citations_count:
            raise ValueError("min_citations_count must be >= len(required_domains_all_of)")
        for c in self.expected_claims:
            if c.acceptable_verdicts and c.primary_verdict not in c.acceptable_verdicts:
                raise ValueError(f"claim {c.claim!r}: primary_verdict not in acceptable_verdicts")
        return self


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
    scenario: str = Field(
        ...,
        description="Scenario code: A1, A2, B, C, D, E, F, G, H, I, J (MSZ section 6); "
        "extensions: K (PII masking), L (out of scope / unable to verify), M (multi-claim)",
    )
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
    reference_datetime: Optional[str] = Field(
        default=None,
        description="ISO 8601 'now' for the case; the runner passes it to the system so relative time words "
        "('dziś', 'ostatnio') are interpreted against a frozen moment. Required when a snapshot fixture is used",
    )
    counts_toward_verdict_metrics: bool = Field(
        default=True,
        description="False for cases that duplicate another case's query (e.g. scenario G cross-channel checks); "
        "they are scored only for consistency, not counted again in macro-F1 / abstention",
    )
    input: TestCaseInput
    environment_overrides: EnvironmentOverrides = Field(default_factory=EnvironmentOverrides)
    deterministic_invariants: DeterministicInvariants
    llm_judge_rubric: LLMJudgeRubric

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_input(self) -> "TestCase":
        for text in (self.input.query, self.input.attachment_url or ""):
            if any(m in text for m in _LOCAL_HOST_MARKERS):
                raise ValueError("input must not expose local fixture URLs; use a realistic URL + mock_untrusted_url_fixture")
        if self.environment_overrides.mock_trusted_api_fixture and not self.reference_datetime:
            raise ValueError("reference_datetime is required when mock_trusted_api_fixture (a frozen snapshot) is used")
        if self.deterministic_invariants.expected_system_status.value != "VERDICT_RENDERED" and self.deterministic_invariants.primary_verdict:
            raise ValueError("primary_verdict must be null unless expected_system_status is VERDICT_RENDERED")
        return self


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


class ClaimVerdict(BaseModel):
    claim: str
    verdict: VerdictEnum


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
    claim_verdicts: list["ClaimVerdict"] = Field(default_factory=list)
    persisted_query: Optional[str] = Field(
        default=None, description="Query text as stored in logs/trace after PII masking"
    )
    moderation_ticket_created: bool = False
    moderation_ticket_id: Optional[str] = None
    moderation_ticket_group_id: Optional[str] = None
    error_message: Optional[str] = None
