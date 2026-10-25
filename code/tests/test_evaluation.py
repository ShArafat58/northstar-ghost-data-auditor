"""Independent synthetic regression tests for the Step 7 diagnostic evaluator."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
import sys
import tempfile
import unittest
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CODE_DIR / "evaluation"))

from evaluation.metrics import CaseRow, evaluate, read_rows, validate_row
from evaluation.main import render_report, run_custom_evaluation, step

EVIDENCE = frozenset({"synthetic_evidence"})


class EvaluationTests(unittest.TestCase):
    def test_three_synthetic_evaluation_cases(self) -> None:
        scenarios = run_custom_evaluation()
        self.assertEqual(len(scenarios), 3)
        self.assertTrue(all(ok for _, ok, _ in scenarios))

    def test_exact_predictions_score_perfectly(self) -> None:
        steps = (step(1, "VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT", "synthetic_audit", "synthetic_evidence"),)
        gold = {"custom_retained": CaseRow("custom_retained", "COMPLETE", steps)}
        outcome = evaluate(gold, gold, evidence_ids=EVIDENCE)
        self.assertEqual(outcome.correct_status, 1)
        self.assertEqual(outcome.correct_rows, 1)
        self.assertEqual(outcome.correct_sequence, 1)
        self.assertEqual(outcome.matched_steps, 1)
        self.assertEqual(outcome.field_correct["evidence_id"], 1)

    def test_missing_predictions_lower_coverage_and_accuracy(self) -> None:
        gold = {
            "custom_a": CaseRow("custom_a", "NOT_APPLICABLE", ()),
            "custom_b": CaseRow("custom_b", "NOT_APPLICABLE", ()),
        }
        outcome = evaluate(gold, {"custom_a": gold["custom_a"]}, evidence_ids=EVIDENCE)
        self.assertEqual(outcome.covered, 1)
        self.assertEqual(outcome.correct_status, 1)
        self.assertEqual(outcome.correct_rows, 1)
        self.assertEqual(outcome.missing_cases, ["custom_b"])
        self.assertEqual(len(outcome.details), 1)

    def test_reordered_steps_have_exact_matches_but_fail_sequence(self) -> None:
        first = step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "synthetic_a", "synthetic_evidence")
        second = step(2, "VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT", "synthetic_b", "synthetic_evidence")
        gold = CaseRow("custom_order", "COMPLETE", (first, second))
        reversed_steps = (dict(second, step_no=1), dict(first, step_no=2))
        predicted = CaseRow("custom_order", "COMPLETE", reversed_steps)
        outcome = evaluate({gold.case_id: gold}, {predicted.case_id: predicted}, evidence_ids=EVIDENCE)
        self.assertEqual(outcome.matched_steps, 2)
        self.assertEqual(outcome.correct_sequence, 0)
        self.assertEqual(outcome.correct_rows, 0)
        self.assertTrue(outcome.details[0]["errors"])

    def test_invalid_evidence_reference_is_detected(self) -> None:
        candidate = CaseRow("custom_bad", "COMPLETE", (
            step(1, "VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT", "synthetic_audit", "fabricated_evidence"),
        ))
        errors = validate_row(candidate, EVIDENCE, frozenset(), frozenset())
        self.assertEqual(len(errors), 1)
        self.assertIn("evidence", errors[0].lower())

    def test_future_evidence_is_rejected_at_audit_cutoff(self) -> None:
        candidate = CaseRow("custom_temporal", "COMPLETE", (
            step(1, "VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT", "synthetic_audit", "synthetic_evidence"),
        ))
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later = datetime(2026, 1, 2, tzinfo=timezone.utc)
        problems = validate_row(
            candidate, EVIDENCE, frozenset(), frozenset(),
            audit_at=cutoff, proof_times={"synthetic_evidence": later},
        )
        self.assertTrue(any("Future evidence" in message for message in problems))

    def test_csv_json_and_duplicate_detection(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fake_predictions.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(("case_id", "deletion_status", "steps_json"))
                writer.writerow(("custom_1", "NOT_APPLICABLE", json.dumps([])))
            rows = read_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows["custom_1"].errors)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("custom_1,NOT_APPLICABLE,[]\n")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                read_rows(path)

    def test_report_includes_complete_mismatch_and_ab_comparison(self) -> None:
        gold = {"custom": CaseRow("custom", "NOT_APPLICABLE", ())}
        a = evaluate(gold, {}, evidence_ids=EVIDENCE)
        b = evaluate(gold, gold, evidence_ids=EVIDENCE)
        report = render_report(a, predictions_path=Path("a.csv"), custom_results=run_custom_evaluation(), comparison=(Path("b.csv"), b))
        self.assertIn("Side-by-side A/B comparison", report)
        self.assertIn("custom", report)
        self.assertIn("Coverage gate", report)


if __name__ == "__main__":
    unittest.main()
