"""Claim splitting and evidence-based verification."""

from app.verification.context import (
    VerificationContext,
    VerificationSource,
    VerificationSourceType,
    build_verification_context,
)
from app.verification.verifier import (
    ClaimSpan,
    LexicalVerifier,
    SemanticVerifier,
    is_insufficient_answer,
    is_insufficient_claim,
    is_nonfactual_claim,
    split_claim_spans,
    split_claims,
)

__all__ = [
    "ClaimSpan",
    "LexicalVerifier",
    "SemanticVerifier",
    "is_insufficient_answer",
    "is_insufficient_claim",
    "is_nonfactual_claim",
    "split_claims",
    "split_claim_spans",
    "VerificationContext",
    "VerificationSource",
    "VerificationSourceType",
    "build_verification_context",
]
