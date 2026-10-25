"""Synthetic regression tests: provenance, policy, ordering, cutoff, and safety."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evidence import retrieve_evidence
from identity import ResolvedIdentity
from loaders import EvidenceFile
from planner import one_record_plan
from validator import validate_prediction


def utc(day: int) -> datetime:
    return datetime(2026, 9, day, tzinfo=timezone.utc)


class Step6Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sql = self.root / "snapshot.sql"
        self.sql.write_text(
            "CREATE TABLE customer_profiles_failover_replica ("
            "resource_id TEXT,user_id TEXT,email TEXT,account_status TEXT);"
            "INSERT INTO customer_profiles_failover_replica VALUES"
            "('account_a','user_05','person@example.test','ACTIVE'),"
            "('account_b','user_050','decoy@example.test','ACTIVE');",
            encoding="utf-8",
        )
        self.file = EvidenceFile("evidence_test", self.sql, "test_snapshot", utc(2))
        self.identity = ResolvedIdentity(
            "user_05", "person@example.test", ("person@example.test",), (), (), (), "snapshot"
        )
        self.rules = ({"data_category": "USER_PROFILE", "condition": "Always", "required_action": "DELETE"},)

    def test_exact_matching_avoids_neighboring_user(self) -> None:
        scan = retrieve_evidence((self.file,), self.identity, utc(3))
        self.assertEqual([hit.resource_id for hit in scan.hits], ["account_a"])

    def test_future_snapshot_is_excluded(self) -> None:
        scan = retrieve_evidence((self.file,), self.identity, utc(1))
        self.assertFalse(scan.hits)
        self.assertFalse(scan.scanned_files)

    def test_policy_grounded_plan_has_valid_steps(self) -> None:
        hit = retrieve_evidence((self.file,), self.identity, utc(3)).hits[0]
        status, steps = one_record_plan(hit, self.rules, evidence_ids=frozenset({self.file.file_id}))
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([step["operation"] for step in steps], [
            "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"
        ])
        self.assertEqual([step["evidence_id"] for step in steps], ["evidence_test", "", ""])
        validate_prediction(status, steps, evidence_ids=frozenset({"evidence_test"}))

    def test_unconditional_policy_is_required(self) -> None:
        hit = retrieve_evidence((self.file,), self.identity, utc(3)).hits[0]
        bad_rule = ({"data_category": "USER_PROFILE", "condition": "Only if requested", "required_action": "DELETE"},)
        with self.assertRaises(ValueError):
            one_record_plan(hit, bad_rule, evidence_ids=frozenset({self.file.file_id}))

    def test_pdf_is_explicitly_uninspected(self) -> None:
        pdf = self.root / "placeholder.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        file = EvidenceFile("evidence_pdf", pdf, "test_pdf", utc(2))
        scan = retrieve_evidence((file,), self.identity, utc(3))
        self.assertEqual(scan.unsupported_files, ("evidence_pdf",))


if __name__ == "__main__":
    unittest.main()
