"""Diagnostic sample evaluator with coverage, sequence, field, and A/B metrics.

Run from any working directory:
    python code/evaluation/main.py --predictions code/evaluation/smoke_predictions.csv --allow-partial

Only this evaluation entry point reads dataset/sample_output.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

CODE_DIR = Path(__file__).resolve().parents[1]
ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from metrics import CaseRow, Evaluation, evaluate, f1, metric_text, ratio, read_rows, validate_row  # noqa: E402
from loaders import parse_timestamp  # noqa: E402


CUSTOM_SCENARIOS = (
    ("custom_no_deletion", "No deletion authorization means NOT_APPLICABLE with no steps."),
    ("custom_remaining_profile", "A verified remaining profile must be reported, removed, then checked."),
    ("custom_retention_preserved", "A policy-required retained audit record must remain with cited proof."),
)


def step(number: int, phase: str, operation: str, state: str, resource: str, evidence: str) -> dict[str, Any]:
    return {
        "step_no": number, "phase": phase, "system_id": "synthetic_service",
        "resource_id": resource, "operation": operation, "state": state, "evidence_id": evidence,
    }


def run_custom_evaluation() -> list[tuple[str, bool, str]]:
    """Three synthetic cases (never used for training or inference shortcuts)."""
    observations: list[tuple[str, bool, str]] = []
    eids = frozenset({"synthetic_evidence"})
    examples = [
        ("custom_no_deletion", "NOT_APPLICABLE", []),
        ("custom_remaining_profile", "INCOMPLETE", [
            step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "synthetic_profile", "synthetic_evidence"),
            step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT", "synthetic_profile", ""),
            step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "synthetic_profile", ""),
        ]),
        ("custom_retention_preserved", "COMPLETE", [
            step(1, "VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT", "synthetic_audit", "synthetic_evidence"),
        ]),
    ]
    for case_id, status, steps in examples:
        reference = CaseRow(case_id, status, tuple(steps))
        candidate = CaseRow(case_id, status, tuple(steps))
        errors = validate_row(candidate, eids, frozenset(), frozenset())
        result = evaluate({case_id: reference}, {case_id: candidate}, evidence_ids=eids)
        passed = not errors and result.correct_rows == 1 and result.correct_status == 1
        observations.append((case_id, passed, "; ".join(errors) if errors else "Exact synthetic reference match"))
    return observations


def read_reference_cases(path: Path) -> dict[str, datetime]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != (
            "case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"
        ):
            raise ValueError("Sample request columns do not match the official contract")
        rows = list(reader)
        result = {row["case_id"]: parse_timestamp(row["audit_at"], context=row["case_id"]) for row in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate case IDs in sample requests")
    return result


def read_authorized_evidence(repo_root: Path) -> tuple[frozenset[str], frozenset[str], frozenset[str], dict[str, datetime]]:
    names: list[frozenset[str]] = []
    timestamps: dict[str, datetime] = {}
    for filename, column, time_field in (
        ("evidence_files.csv", "file_id", "captured_at"),
        ("system_activity.csv", "activity_id", "event_time"),
        ("user_audit_log.csv", "audit_id", "event_time"),
    ):
        with (repo_root / "dataset" / filename).open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        names.append(frozenset(row[column] for row in rows))
        for row in rows:
            timestamps[row[column]] = parse_timestamp(row[time_field], context=row[column])
    return names[0], names[1], names[2], timestamps


def confusion_table(counts: Counter[tuple[str, str]]) -> str:
    lines = ["| Expected status | Predicted status | Cases |", "|---|---|---:|"]
    for (expected, predicted), n in sorted(counts.items()):
        lines.append(f"| `{expected}` | `{predicted}` | {n} |")
    return "\n".join(lines)


def classification_table(outcome: Evaluation) -> str:
    classes = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
    lines = ["| Status | Precision | Recall | F1 | Support |", "|---|---:|---:|---:|---:|"]
    for label in classes:
        tp = outcome.status_confusion.get((label, label), 0)
        pred = sum(n for (_, predicted), n in outcome.status_confusion.items() if predicted == label)
        actual = sum(n for (expected, _), n in outcome.status_confusion.items() if expected == label)
        lines.append(f"| `{label}` | {ratio(tp, pred)} | {ratio(tp, actual)} | {f1(tp, pred, actual)} | {actual} |")
    return "\n".join(lines)


def operation_table(outcome: Evaluation) -> str:
    operations = sorted(set(outcome.operation_truth) | set(outcome.operation_predicted))
    lines = ["| Operation | Exact-resource step precision | Exact-resource step recall |", "|---|---:|---:|"]
    for operation in operations:
        tp = outcome.operation_matched[operation]
        lines.append(f"| `{operation}` | {ratio(tp, outcome.operation_predicted[operation])} | {ratio(tp, outcome.operation_truth[operation])} |")
    return "\n".join(lines) if operations else "No steps in either input."


def render_report(
    outcome: Evaluation,
    *,
    predictions_path: Path,
    custom_results: list[tuple[str, bool, str]],
    comparison: tuple[Path, Evaluation] | None = None,
) -> str:
    lines = [
        "# Evaluation Report", "",
        "## Dataset and provenance", "",
        f"- Labeled reference: `dataset/sample_output.csv` ({outcome.total} cases).",
        f"- Prediction input: `{predictions_path.name}`.",
        "- Labels are read only by this evaluator. No inference component loads the reference file.",
        "- All reported results are computed from the actual input predictions; no estimates are inserted.", "",
        "## Core metrics", "",
        "| Metric | Observed |", "|---|---:|",
        f"| Prediction coverage | {ratio(outcome.covered, outcome.total)} |",
        f"| Contract-valid predicted rows | {ratio(outcome.valid, outcome.covered)} |",
        f"| Case-level status accuracy (missing=incorrect) | {ratio(outcome.correct_status, outcome.total)} |",
        f"| Exact step precision, order-independent | {ratio(outcome.matched_steps, outcome.predicted_steps)} |",
        f"| Exact step recall, order-independent | {ratio(outcome.matched_steps, outcome.expected_steps)} |",
        f"| Exact step F1 | {f1(outcome.matched_steps, outcome.predicted_steps, outcome.expected_steps)} |",
        f"| Whole-case sequence accuracy | {ratio(outcome.correct_sequence, outcome.total)} |",
        f"| Exact full-row accuracy | {ratio(outcome.correct_rows, outcome.total)} |",
        "",
        "**Definitions:** Exact steps match on phase, system_id, resource_id, operation, state, and evidence_id using multiset matching. Sequence requires the entire ordered step array, including consecutive step_no, to match. Field accuracy compares corresponding ordinal steps; missing and extra steps contribute incorrect fields. Every missing case is wrong, not excluded from the denominator.",
        "",
    ]
    if outcome.covered < outcome.total:
        lines += [
            "**Coverage gate: NOT A FULL-BASELINE SCORE.** Some labelled cases have no predictions. This is only a partial diagnostic run; produce predictions for all sample cases before using accuracy to select a model or ship.",
            "",
        ]
    lines += ["## Field-level accuracy", "", "| Field | Exact positional accuracy |", "|---|---:|"]
    for field in (
        "step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"
    ):
        lines.append(f"| `{field}` | {ratio(outcome.field_correct[field], outcome.field_total[field])} |")
    lines += ["", "## Status precision and recall", "", classification_table(outcome), "", "## Status confusion pairs", "", confusion_table(outcome.status_confusion), "", "## Step operation precision and recall", "", operation_table(outcome), "", "## Custom synthetic evaluation cases", "", "| Case | Scenario | Result |", "|---|---|---|"]
    for (case_id, desc), (_, passed, _) in zip(CUSTOM_SCENARIOS, custom_results):
        lines.append(f"| `{case_id}` | {desc} | {'PASS' if passed else 'FAIL'} |")
    lines += [
        "",
        "These three additional cases validate the evaluator and output-contract handling. They do not establish end-to-end agent performance on unseen evidence.",
        "",
        "## Failures and diagnostic findings", "",
        f"- Cases with any mismatch or invalid output: {len(outcome.details)}.",
        f"- Missing predictions: {len(outcome.missing_cases)}.",
        f"- Predictions with unknown case IDs: {len(outcome.extra_cases)}.",
        "- Each error is listed below and written to `mismatches.jsonl` with per-position expected/predicted objects.",
        "- Improvement implemented in Step 7: strict CSV/schema checks, per-field scoring, operation-level error diagnostics, missing-case penalties and an A/B evaluation interface.",
        "- Next improvement is conditional on measured complete-sample errors. No agent accuracy improvement is claimed yet.",
        "",
        "### Complete mismatch index", "",
        "| Case | Expected | Predicted | Matching steps | Expected/predicted steps | Error |",
        "|---|---|---|---:|---:|---|",
    ]
    for detail in outcome.details:
        problem = ", ".join(detail["errors"]) if detail["errors"] else "value/order mismatch"
        problem = problem.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{detail['case_id']}` | `{detail['expected_status']}` | `{detail['predicted_status']}` | "
            f"{detail['exact_step_matches']} | {detail['expected_step_count']}/{detail['predicted_step_count']} | {problem} |"
        )
    if outcome.extra_cases:
        lines += ["", "Extra prediction IDs: " + ", ".join(f"`{case}`" for case in outcome.extra_cases)]
    if comparison:
        other_path, other = comparison
        lines += ["", "## Side-by-side A/B comparison", "", f"A: `{predictions_path.name}`. B: `{other_path.name}`.", "", "| Metric | A | B | Change (B-A) |", "|---|---:|---:|---:|"]
        for label, a, b in (
            ("Coverage", outcome.covered, other.covered),
            ("Status correct", outcome.correct_status, other.correct_status),
            ("Exact step hits", outcome.matched_steps, other.matched_steps),
            ("Exact sequences", outcome.correct_sequence, other.correct_sequence),
            ("Exact rows", outcome.correct_rows, other.correct_rows),
            ("Exact field positions", sum(outcome.field_correct.values()), sum(other.field_correct.values())),
        ):
            lines.append(f"| {label} | {a} | {b} | {b-a:+d} |")
        a_errors = {d["case_id"] for d in outcome.details}
        b_errors = {d["case_id"] for d in other.details}
        fixed = sorted(a_errors - b_errors)
        regressed = sorted(b_errors - a_errors)
        lines += ["", "Fixed exact cases: " + (", ".join(f"`{x}`" for x in fixed) or "None"), "", "Regressed exact cases: " + (", ".join(f"`{x}`" for x in regressed) or "None"), ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, help="CSV generated by the solution on 32 sample requests")
    parser.add_argument("--compare", type=Path, help="Optional second predictions CSV for A/B comparisons")
    parser.add_argument("--allow-partial", action="store_true", help="Diagnose a partial smoke run without treating it as a complete baseline")
    parser.add_argument("--custom-only", action="store_true", help="Run three synthetic evaluator checks only")
    parser.add_argument("--report", type=Path, default=Path(__file__).parent / "evaluation_report.md")
    args = parser.parse_args()

    custom_results = run_custom_evaluation()
    for case_id, passed, note in custom_results:
        print(f"CUSTOM {case_id}: {'PASS' if passed else 'FAIL'} - {note}")
    if not all(success for _, success, _ in custom_results):
        raise RuntimeError("Custom evaluation scenarios failed")
    if args.custom_only:
        return 0
    if args.predictions is None:
        parser.error("--predictions is required unless --custom-only is used")
    if args.compare is not None and not args.compare.is_file():
        parser.error(f"Missing comparison file: {args.compare}")

    gold = read_rows(ROOT / "dataset" / "sample_output.csv")
    cutoffs = read_reference_cases(ROOT / "dataset" / "sample_deletion_requests.csv")
    sample_ids = set(cutoffs)
    if set(gold) != sample_ids:
        raise ValueError("Sample output and sample request identifiers do not match")
    if len(gold) != 32:
        raise ValueError(f"Expected 32 labeled sample cases; got {len(gold)}")
    predictions = read_rows(args.predictions)
    if not args.allow_partial and set(predictions) != set(gold):
        raise ValueError(
            f"A full evaluation requires all 32 IDs (missing={len(set(gold)-set(predictions))}, "
            f"extra={len(set(predictions)-set(gold))}). Use --allow-partial only for smoke diagnostics."
        )
    evidence, activities, audits, proof_times = read_authorized_evidence(ROOT)
    outcome = evaluate(gold, predictions, evidence_ids=evidence, activity_ids=activities, audit_ids=audits, cutoffs=cutoffs, proof_times=proof_times)
    comparison = None
    if args.compare:
        other_pred = read_rows(args.compare)
        if not args.allow_partial and set(other_pred) != set(gold):
            raise ValueError("Comparison CSV must also cover all 32 labelled cases")
        comparison = (args.compare, evaluate(gold, other_pred, evidence_ids=evidence, activity_ids=activities, audit_ids=audits, cutoffs=cutoffs, proof_times=proof_times))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(outcome, predictions_path=args.predictions, custom_results=custom_results, comparison=comparison), encoding="utf-8")
    mismatch_file = args.report.parent / "mismatches.jsonl"
    with mismatch_file.open("w", encoding="utf-8", newline="\n") as output:
        for detail in outcome.details:
            output.write(json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(metric_text(outcome))
    print(f"REPORT: {args.report}")
    print(f"MISMATCH DETAILS: {mismatch_file}")
    if outcome.covered != outcome.total:
        print("PARTIAL RUN ONLY: sample accuracy is not a full-baseline result.")
    if comparison:
        print("A/B COMPARISON SAVED IN REPORT")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"EVALUATION ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
