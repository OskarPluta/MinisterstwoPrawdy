"""
Prawdowid Evaluation Package (`eval`).

Provides Pydantic schemas, verified benchmark dataset, and controlled test fixtures
aligned with MSZ competition requirements.
"""

from eval.schema import (
    TestCase,
    TestCaseInput,
    EnvironmentOverrides,
    DeterministicInvariants,
    LLMJudgeRubric,
    EvidenceChunk,
    Citation,
    SystemExecutionTrace,
    VerdictEnum,
    SystemStatusEnum,
    OriginEnum,
    TicketActionEnum,
    ChannelEnum,
)

__all__ = [
    "TestCase",
    "TestCaseInput",
    "EnvironmentOverrides",
    "DeterministicInvariants",
    "LLMJudgeRubric",
    "EvidenceChunk",
    "Citation",
    "SystemExecutionTrace",
    "VerdictEnum",
    "SystemStatusEnum",
    "OriginEnum",
    "TicketActionEnum",
    "ChannelEnum",
]
