"""Baseline tests operate on synthetic data only; no labelled answer leakage."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline import authorizes_deletion, plan_case
from identity import ResolvedIdentity
from loaders import DeletionRequest
from observations import EvidenceIndex, Observation, state_from_record
from validator import validate_prediction

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
RULES = (
    {"data_category": "USER_PROFILE", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "ANALYTICS_PROFILE", "condition": "still attributable", "required_action": "ANONYMIZE"},
    {"data_category": "SECURITY_AUDIT_LOG", "condition": "retention", "required_action": "RETAIN"},
    {"data_category": "BACKUP_ARCHIVE", "condition": "restore can be blocked", "required_action": "RETAIN"},
)
IDENTITY = ResolvedIdentity("user_7", "a@example.com", ("a@example.com",), (), (), (), "snapshot")


def request(message: str = "Please delete my account") -> DeletionRequest:
    return DeletionRequest("case_demo", "a@example.com", T0, "NOT_STARTED", T0, T0 + timedelta(days=2), message)


def observation(resource: str, category: str, state: str, ts: datetime = T0 + timedelta(days=1), keys: tuple[str, ...] = ("user_7",), file_id: str = "evidence_01") -> Observation:
    return Observation(file_id, ts, resource,
                       "backup_service" if category == "BACKUP_ARCHIVE" else "security_service" if category == "SECURITY_AUDIT_LOG" else "account_service" if category == "USER_PROFILE" else "analytics_platform",
                       category, state, keys, "synthetic")


class BaselineTests(unittest.TestCase):
    def test_non_deletion(self) -> None:
        self.assertFalse(authorizes_deletion("Please export my data without deleting my account"))
        self.assertTrue(authorizes_deletion("Please delete my account"))

    def test_ordered_remediation_and_retention(self) -> None:
        data = EvidenceIndex((
            observation("account_4", "USER_PROFILE", "PRESENT"),
            observation("backup_4", "BACKUP_ARCHIVE", "ACTIVE"),
            observation("audit_record_4", "SECURITY_AUDIT_LOG", "PRESENT"),
        ), (), ("evidence_01",))
        plan = plan_case(request(), IDENTITY, data, RULES)
        self.assertEqual(plan.status, "INCOMPLETE")
        self.assertEqual([s["operation"] for s in plan.steps], [
            "REPORT_REMAINING_RECORD", "REPORT_RESTORE_RISK", "BLOCK_RESTORE",
            "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT",
            "VERIFY_RESTORE_BLOCKED",
        ])
        validate_prediction(plan.status, plan.steps, evidence_ids=frozenset({"evidence_01"}))

    def test_later_404_overrides_earlier_present(self) -> None:
        data = EvidenceIndex((
            observation("account_4", "USER_PROFILE", "PRESENT"),
            observation("account_4", "USER_PROFILE", "ABSENT", T0 + timedelta(days=2)),
        ), (), ("evidence_01",))
        plan = plan_case(request(), IDENTITY, data, RULES)
        self.assertEqual(plan.status, "COMPLETE")
        self.assertEqual([step["operation"] for step in plan.steps], ["VERIFY_RECORD_ABSENT"])

    def test_anonymized_record_stays_linked_by_resource(self) -> None:
        data = EvidenceIndex((
            observation("analytics_2", "ANALYTICS_PROFILE", "PRESENT"),
            observation("analytics_2", "ANALYTICS_PROFILE", "ANONYMIZED", T0 + timedelta(days=2), ()),
        ), (), ("evidence_01",))
        plan = plan_case(request(), IDENTITY, data, RULES)
        self.assertEqual(plan.status, "COMPLETE")
        self.assertEqual(plan.steps[0]["operation"], "VERIFY_RECORD_ANONYMIZED")

    def test_state_extraction(self) -> None:
        self.assertEqual(state_from_record({"enabled": 0}, "DATA_FLOW"), "DISABLED")
        self.assertEqual(state_from_record({"restore_allowed": 1}, "BACKUP_ARCHIVE"), "ACTIVE")
        self.assertEqual(state_from_record({"rows": 0}, "USER_PROFILE"), "ABSENT")


if __name__ == "__main__":
    unittest.main()
