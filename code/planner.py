"""Conservative preliminary plan for one evidenced, unconditional policy violation."""

from __future__ import annotations

from evidence import EvidenceHit
from policy import unconditional_action
from validator import sort_steps, validate_prediction

ACTIONS: dict[str, tuple[str, str, str]] = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
}
STATES = {
    "REPORT_REMAINING_RECORD": "PRESENT",
    "REPORT_EXPOSED_PII": "PRESENT",
    "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED",
    "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT",
    "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
}


def one_record_plan(
    hit: EvidenceHit,
    rules: tuple[dict[str, str], ...],
    *,
    evidence_ids: frozenset[str],
) -> tuple[str, list[dict[str, object]]]:
    """Produce a valid provisional row; never present it as a full audit.

    A snapshot row proves a record exists but not that *all* other copies are
    accounted for. Only an unconditional DELETE policy is supported here.
    """
    if hit.source != "sql" or not hit.verified_record or hit.state != "PRESENT":
        raise ValueError("The smoke planner needs a present, SQL-verified record")
    if hit.category is None or unconditional_action(rules, hit.category) != "DELETE":
        raise ValueError("No unconditional DELETE rule applies to this observed record")
    finding, remediation, verification = ACTIONS["DELETE"]
    steps = [
        {
            "step_no": 0,
            "phase": phase,
            "system_id": hit.system_id,
            "resource_id": hit.resource_id,
            "operation": operation,
            "state": STATES[operation],
            "evidence_id": hit.file_id if phase == "FINDING" else "",
        }
        for phase, operation in (
            ("FINDING", finding),
            ("REMEDIATION", remediation),
            ("VERIFICATION", verification),
        )
    ]
    ordered = sort_steps(steps)
    for number, step in enumerate(ordered, start=1):
        step["step_no"] = number
    validate_prediction("INCOMPLETE", ordered, evidence_ids=evidence_ids)
    return "INCOMPLETE", ordered
