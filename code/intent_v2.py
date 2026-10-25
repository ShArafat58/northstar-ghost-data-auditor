"""Generalized authorization for deletion requests, review and verification.

This module is participant-facing request-text logic only. It never reads labels
or case identifiers. Negative instructions take precedence over keyword hits.
"""

from __future__ import annotations

import re

from baseline import NEGATIVE, POSITIVE

# Explicit opt-outs and corrections: mentioning deletion is not consent.
ADDITIONAL_NEGATIVE = re.compile(
    r"\b(?:do\s+not|don'?t|never|not\s+asking\s+(?:you\s+)?to|"
    r"not\s+requesting|without|instead\s+of)\s+"
    r"(?:(?:want|need|intend)\s+to\s+)?"
    r"(?:delete|deleting|deletion|erase|erasing|erasure|remove|removing|removal|close|closing|closure)\b|"
    r"\b(?:cancel|withdraw|revoke)\s+(?:my\s+|the\s+)?"
    r"(?:deletion|erasure|account\s+closure)\s+request\b|"
    r"\b(?:keep|retain|preserve)\s+(?:my\s+|the\s+)?account\b",
    re.I,
)

# Actual scope, not incidental mention of a deletion or a generic support request.
REMOVE_DATA = re.compile(
    r"\b(?:remove|erase|delete|clear\s+out|wipe)\s+"
    r"(?:(?:all|any|the|my|our|this|that|those|these|remaining|old)\s+){0,3}"
    r"(?:accounts?|profiles?|records?|search\s+(?:results?|entries|index)|"
    r"personal\s+(?:data|details|information)|identity\s+(?:documents?|files?)|"
    r"(?:downloadable\s+)?archives?|stored\s+(?:data|information)|"
    r"contact\s+(?:details|information)|names?\s+and\s+contact\s+details)\b",
    re.I,
)

CLOSE_ACCOUNT = re.compile(
    r"\b(?:my|the|this|our)\s+accounts?\s+"
    r"(?:should\s+be|to\s+be|was\s+already|has\s+been|is\s+being|is\s+now)\s+"
    r"(?:closed|deleted|removed|erased)\b|"
    r"\b(?:would\s+like|want|wish|need|request(?:ing)?)\s+"
    r"(?:it|the\s+account|my\s+account|this\s+account)?\s*"
    r"(?:to\s+be\s+)?(?:closed|deleted|removed|erased)\b",
    re.I,
)

# Verify a prior deletion / investigate failed privacy execution is in scope even
# if the customer is not asking the operator to perform deletion a second time.
DELETION_AUDIT = re.compile(
    r"\b(?:verify|confirm|check|inspect|audit|review|investigate|tell\s+me)\b"
    r"[^.!?]{0,180}?"
    r"\b(?:delet(?:ed|ion)|eras(?:ed|ure)|remov(?:ed|al)|"
    r"(?:account|profile)\s+(?:is|was)\s+gone|"
    r"(?:account|profile)\s+(?:has\s+been|was)\s+(?:closed|deleted))\b",
    re.I,
)


def authorizes_deletion_v2(message: str) -> bool:
    """Accept explicit data deletion or its audit; reject explicit opt-outs.

    Generic mentions ("the deletion policy exists") alone are not authorization.
    Baseline multilingual expressions remain supported for explicit requests.
    """
    if NEGATIVE.search(message) or ADDITIONAL_NEGATIVE.search(message):
        return False
    return bool(
        POSITIVE.search(message)
        or REMOVE_DATA.search(message)
        or CLOSE_ACCOUNT.search(message)
        or DELETION_AUDIT.search(message)
    )
