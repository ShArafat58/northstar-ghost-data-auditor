"""Regression tests for the label-free evidence-gap triage tool."""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from audit_evidence_gaps import aliases_for, exact_token_in_text, find_alias_types, find_raw_candidates
from identity import ResolvedIdentity
from loaders import DeletionRequest, EvidenceFile


UTC = timezone.utc
D = datetime(2026, 9, 1, tzinfo=UTC)


class EvidenceGapTests(unittest.TestCase):
    def test_token_boundaries(self):
        self.assertTrue(exact_token_in_text("subject=user_7", "user_7"))
        self.assertFalse(exact_token_in_text("subject=user_70", "user_7"))
        self.assertFalse(exact_token_in_text("afoo@example.com", "foo@example.com"))
        self.assertTrue(exact_token_in_text("lookup=foo@example.com", "foo@example.com"))

    def test_only_alias_type_revealed(self):
        identity = ResolvedIdentity("user_7", "foo@example.com", ("foo@example.com",), (), (), (), "snapshot")
        types = find_alias_types("email=foo@example.com", aliases_for(identity))
        self.assertEqual(types, ["email"])

    def test_cutoff_and_exact_identity(self):
        identity = ResolvedIdentity("user_7", "foo@example.com", ("foo@example.com",), (), (), (), "snapshot")
        req = DeletionRequest("case_zz", "foo@example.com", D, "COMPLETE", D, D, "Delete my account")
        with tempfile.TemporaryDirectory() as tmp:
            early = Path(tmp) / "early.log"
            early.write_text("resource_id=account_999 email=foo@example.com rows=1", encoding="utf-8")
            future = Path(tmp) / "future.log"
            future.write_text("email=foo@example.com", encoding="utf-8")
            wrong = Path(tmp) / "wrong.log"
            wrong.write_text("email=xfoo@example.com", encoding="utf-8")
            files = [EvidenceFile("e1", early, "local", D),
                     EvidenceFile("e2", future, "local", datetime(2026, 9, 2, tzinfo=UTC)),
                     EvidenceFile("e3", wrong, "local", D)]
            found = find_raw_candidates(req, identity, files, use_ocr=False)
            self.assertEqual([x["evidence_id"] for x in found], ["e1"])
            self.assertEqual(found[0]["same_line_resource_candidates"], ["account_999"])


if __name__ == "__main__":
    unittest.main()
