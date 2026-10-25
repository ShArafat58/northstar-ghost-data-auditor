"""Strict, reference-only evaluation of deletion-audit CSV predictions.

This module is NEVER imported by the prediction pipeline. Labeled answers stay
inside the evaluation directory and must not influence inference or retrieval.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from validator import OPERATIONS, VALID_STATUSES, validate_prediction

COLUMNS = ("case_id", "deletion_status", "steps_json")
STEP_FIELDS = (
    "step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id",
)
STEP_SIGNATURE = tuple(field for field in STEP_FIELDS if field != "step_no")


@dataclass(frozen=True)
class CaseRow:
    case_id: str
    status: str
    steps: tuple[dict[str, Any], ...]
    errors: tuple[str, ...] = ()


@dataclass
class Evaluation:
    total: int
    covered: int
    valid: int
    missing_cases: list[str]
    extra_cases: list[str]
    correct_status: int
    correct_sequence: int
    correct_rows: int
    matched_steps: int
    predicted_steps: int
    expected_steps: int
    field_correct: Counter[str]
    field_total: Counter[str]
    status_confusion: Counter[tuple[str, str]]
    operation_truth: Counter[str]
    operation_predicted: Counter[str]
    operation_matched: Counter[str]
    details: list[dict[str, Any]]


def _parse_steps(value: str) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        return (), (f"Invalid steps_json: {exc}",)
    if not isinstance(result, list):
        return (), ("steps_json must be a JSON array",)
    if not all(isinstance(step, dict) for step in result):
        return (), ("Every step must be a JSON object",)
    return tuple(result), ()


def read_rows(path: Path) -> dict[str, CaseRow]:
    """Read CSV with exact column order. Preserve malformed rows for diagnostics."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, restkey="__EXTRA__")
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"Incorrect CSV header in {path}: {reader.fieldnames}")
        rows: dict[str, CaseRow] = {}
        for line, row in enumerate(reader, start=2):
            case_id = row.get("case_id", "")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError(f"Empty case_id in {path}:{line}")
            if case_id in rows:
                raise ValueError(f"Duplicate case_id {case_id!r} in {path}")
            errors: list[str] = []
            if "__EXTRA__" in row or any(item is None for item in row.values()):
                errors.append("Invalid CSV column count")
            steps, parse_errors = _parse_steps(row.get("steps_json") or "")
            errors.extend(parse_errors)
            rows[case_id] = CaseRow(
                case_id=case_id,
                status=row.get("deletion_status") or "",
                steps=steps,
                errors=tuple(errors),
            )
        return rows


def _step_signature(step: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(step.get(field, "<missing>")) for field in STEP_SIGNATURE)


def _step_diff(expected: tuple[dict[str, Any], ...], predicted: tuple[dict[str, Any], ...]) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    """Multiset matching isolates step correctness from sequence correctness."""
    ec = Counter(_step_signature(step) for step in expected)
    pc = Counter(_step_signature(step) for step in predicted)
    matched = sum((ec & pc).values())
    missing = [dict(zip(STEP_SIGNATURE, signature)) for signature, n in (ec - pc).items() for _ in range(n)]
    unexpected = [dict(zip(STEP_SIGNATURE, signature)) for signature, n in (pc - ec).items() for _ in range(n)]
    return matched, missing, unexpected


def validate_row(
    row: CaseRow,
    evidence_ids: frozenset[str],
    activity_ids: frozenset[str],
    audit_ids: frozenset[str],
    *,
    audit_at: datetime | None = None,
    proof_times: Mapping[str, datetime] | None = None,
) -> tuple[str, ...]:
    errors = list(row.errors)
    if not errors:
        try:
            validate_prediction(
                row.status,
                list(row.steps),
                evidence_ids=evidence_ids,
                activity_ids=activity_ids,
                audit_ids=audit_ids,
            )
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"Output contract: {exc}")
    if audit_at is not None and proof_times is not None:
        for step in row.steps:
            if step.get("phase") == "FINDING" or row.status == "COMPLETE":
                cited = step.get("evidence_id")
                if cited in proof_times and proof_times[cited] > audit_at:
                    errors.append(f"Future evidence cannot prove current state: {cited}")
    return tuple(errors)


def evaluate(
    gold: Mapping[str, CaseRow],
    predictions: Mapping[str, CaseRow],
    *,
    evidence_ids: frozenset[str],
    activity_ids: frozenset[str] = frozenset(),
    audit_ids: frozenset[str] = frozenset(),
    cutoffs: Mapping[str, datetime] | None = None,
    proof_times: Mapping[str, datetime] | None = None,
) -> Evaluation:
    details: list[dict[str, Any]] = []
    matched_steps = predicted_steps = expected_steps = 0
    covered = valid = correct_status = correct_sequence = correct_rows = 0
    field_correct: Counter[str] = Counter()
    field_total: Counter[str] = Counter()
    confusion: Counter[tuple[str, str]] = Counter()
    op_truth: Counter[str] = Counter()
    op_pred: Counter[str] = Counter()
    op_matched: Counter[str] = Counter()
    missing_cases = sorted(set(gold) - set(predictions))
    extra_cases = sorted(set(predictions) - set(gold))

    for case_id in sorted(gold):
        reference = gold[case_id]
        candidate = predictions.get(case_id)
        pred_steps = candidate.steps if candidate else ()
        errors = validate_row(
            candidate, evidence_ids, activity_ids, audit_ids,
            audit_at=cutoffs.get(case_id) if cutoffs else None, proof_times=proof_times,
        ) if candidate else ("Missing prediction",)
        if candidate:
            covered += 1
            if not errors:
                valid += 1
        status_value = candidate.status if candidate else "<MISSING>"
        if candidate and candidate.status not in VALID_STATUSES:
            status_value = "<INVALID>"
        confusion[(reference.status, status_value)] += 1
        status_match = candidate is not None and candidate.status == reference.status
        if status_match:
            correct_status += 1
        step_match, missing_steps, extra_steps = _step_diff(reference.steps, pred_steps)
        matched_steps += step_match
        expected_steps += len(reference.steps)
        predicted_steps += len(pred_steps)
        for step in reference.steps:
            op_truth[str(step.get("operation", "<MISSING>"))] += 1
        for step in pred_steps:
            op_pred[str(step.get("operation", "<MISSING>"))] += 1
        for signature, n in (Counter(_step_signature(s) for s in reference.steps) & Counter(_step_signature(s) for s in pred_steps)).items():
            op_matched[signature[STEP_SIGNATURE.index("operation")]] += n

        for pos in range(max(len(reference.steps), len(pred_steps))):
            expected_step = reference.steps[pos] if pos < len(reference.steps) else None
            actual_step = pred_steps[pos] if pos < len(pred_steps) else None
            for field in STEP_FIELDS:
                field_total[field] += 1
                if expected_step is not None and actual_step is not None:
                    if field in expected_step and field in actual_step and type(expected_step[field]) is type(actual_step[field]) and expected_step[field] == actual_step[field]:
                        field_correct[field] += 1

        sequence_match = bool(candidate) and not errors and (
            tuple(candidate.steps) == tuple(reference.steps)
        )
        if sequence_match:
            correct_sequence += 1
        full_row_match = status_match and sequence_match
        if full_row_match:
            correct_rows += 1
        if errors or not full_row_match:
            differences = []
            for pos in range(max(len(reference.steps), len(pred_steps))):
                ref_step = reference.steps[pos] if pos < len(reference.steps) else None
                pred_step = pred_steps[pos] if pos < len(pred_steps) else None
                if ref_step != pred_step:
                    differences.append({"position": pos + 1, "expected": ref_step, "predicted": pred_step})
            details.append({
                "case_id": case_id,
                "expected_status": reference.status,
                "predicted_status": status_value,
                "errors": list(errors),
                "expected_step_count": len(reference.steps),
                "predicted_step_count": len(pred_steps),
                "exact_step_matches": step_match,
                "sequence_correct": bool(sequence_match),
                "missing_steps": missing_steps,
                "extra_steps": extra_steps,
                "positional_differences": differences,
            })
    return Evaluation(
        total=len(gold), covered=covered, valid=valid,
        missing_cases=missing_cases, extra_cases=extra_cases,
        correct_status=correct_status, correct_sequence=correct_sequence, correct_rows=correct_rows,
        matched_steps=matched_steps, predicted_steps=predicted_steps, expected_steps=expected_steps,
        field_correct=field_correct, field_total=field_total,
        status_confusion=confusion, operation_truth=op_truth,
        operation_predicted=op_pred, operation_matched=op_matched, details=details,
    )


def ratio(num: int, den: int) -> str:
    return "N/A" if den == 0 else f"{num}/{den} ({num / den:.1%})"


def f1(tp: int, predicted: int, expected: int) -> str:
    if not predicted or not expected:
        return "N/A" if predicted == expected == 0 else "0.0%"
    return f"{2 * tp / (predicted + expected):.1%}"


def metric_text(outcome: Evaluation) -> str:
    """Concise console status, preserving sample coverage context."""
    lines = [
        f"COVERAGE: {ratio(outcome.covered, outcome.total)}",
        f"VALID PREDICTIONS: {ratio(outcome.valid, outcome.covered)}",
        f"STATUS ACCURACY (all labelled cases): {ratio(outcome.correct_status, outcome.total)}",
        f"STEP PRECISION: {ratio(outcome.matched_steps, outcome.predicted_steps)}",
        f"STEP RECALL: {ratio(outcome.matched_steps, outcome.expected_steps)}",
        f"STEP F1: {f1(outcome.matched_steps, outcome.predicted_steps, outcome.expected_steps)}",
        f"SEQUENCE ACCURACY: {ratio(outcome.correct_sequence, outcome.total)}",
        f"EXACT ROW ACCURACY: {ratio(outcome.correct_rows, outcome.total)}",
        f"MISMATCHED CASES: {len(outcome.details)}",
    ]
    return "\n".join(lines)
