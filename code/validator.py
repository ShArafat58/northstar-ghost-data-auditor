"""Schema and ordering enforcement for deletion-audit output rows."""

from __future__ import annotations

import json

FIELDS = frozenset({"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"})
OPERATIONS = {
    "FINDING": [
        ("REPORT_REMAINING_RECORD", "PRESENT"),
        ("REPORT_EXPOSED_PII", "PRESENT"),
        ("REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
        ("REPORT_RESTORE_RISK", "ACTIVE"),
    ],
    "REMEDIATION": [
        ("DISABLE_DATA_FLOW", "DISABLED"),
        ("BLOCK_RESTORE", "BLOCKED"),
        ("DELETE_RECORD", "ABSENT"),
        ("REDACT_RECORD", "REDACTED"),
        ("ANONYMIZE_RECORD", "ANONYMIZED"),
    ],
    "VERIFICATION": [
        ("VERIFY_RECORD_ABSENT", "ABSENT"),
        ("VERIFY_RECORD_PRESENT", "PRESENT"),
        ("VERIFY_RECORD_REDACTED", "REDACTED"),
        ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
        ("VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
        ("VERIFY_RESTORE_BLOCKED", "BLOCKED"),
    ],
}
PHASES = tuple(OPERATIONS)
VALID_STATUSES = frozenset({"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"})


def sort_steps(steps: list[dict[str, object]]) -> list[dict[str, object]]:
    """Canonical order is phase, operation, system, resource."""
    return sorted(steps, key=lambda step: (
        PHASES.index(str(step["phase"])),
        [operation for operation, _ in OPERATIONS[str(step["phase"])]].index(str(step["operation"])),
        str(step["system_id"]), str(step["resource_id"]),
    ))


def validate_prediction(
    status: str,
    steps: list[dict[str, object]],
    *,
    evidence_ids: frozenset[str],
    activity_ids: frozenset[str] = frozenset(),
    audit_ids: frozenset[str] = frozenset(),
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid deletion status: {status}")
    if status == "NOT_APPLICABLE" and steps:
        raise ValueError("NOT_APPLICABLE must contain no steps")
    if status == "COMPLETE" and any(step["phase"] != "VERIFICATION" for step in steps):
        raise ValueError("COMPLETE may only have verification steps")
    if status == "INCOMPLETE" and not any(step["phase"] == "FINDING" for step in steps):
        raise ValueError("INCOMPLETE must explain its evidence-supported finding")
    if steps != sort_steps(steps):
        raise ValueError("Steps do not follow canonical phase and operation ordering")
    allowed_evidence = evidence_ids | activity_ids | audit_ids
    for position, step in enumerate(steps, start=1):
        if set(step) != FIELDS:
            raise ValueError(f"Step {position} has invalid keys")
        if type(step["step_no"]) is not int or step["step_no"] != position:
            raise ValueError(f"Step {position} must have consecutive integer step_no")
        phase = step["phase"]
        if phase not in OPERATIONS or (step["operation"], step["state"]) not in OPERATIONS[phase]:
            raise ValueError(f"Illegal phase/operation/state at step {position}")
        if not isinstance(step["system_id"], str) or not step["system_id"]:
            raise ValueError(f"Missing system_id at step {position}")
        if not isinstance(step["resource_id"], str) or not step["resource_id"]:
            raise ValueError(f"Missing resource_id at step {position}")
        evidence_id = step["evidence_id"]
        if not isinstance(evidence_id, str):
            raise ValueError(f"Invalid evidence_id type at step {position}")
        is_current_proof = phase == "FINDING" or status == "COMPLETE"
        if is_current_proof and evidence_id not in allowed_evidence:
            raise ValueError(f"No valid supporting evidence at step {position}")
        if not is_current_proof and evidence_id != "":
            raise ValueError(f"Future action cannot cite existing evidence at step {position}")


def serialize_steps(steps: list[dict[str, object]]) -> str:
    return json.dumps(steps, separators=(",", ":"), ensure_ascii=False)
