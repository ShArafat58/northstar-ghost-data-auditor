"""Request-intent regression tests; synthetic utterances, no sample labels."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline import authorizes_deletion, plan_case
from identity import ResolvedIdentity
from intent_v2 import authorizes_deletion_v2
from loaders import DeletionRequest
from observations import EvidenceIndex, Observation
from validator import validate_prediction


class IntentV2Tests(unittest.TestCase):
    def test_explicit_remove_existing_profile(self) -> None:
        self.assertTrue(authorizes_deletion_v2("Please remove the profile and its personal details."))

    def test_remove_search_result(self) -> None:
        self.assertTrue(authorizes_deletion_v2("Can you remove that search result too?"))

    def test_remove_records_related_to_alias(self) -> None:
        self.assertTrue(authorizes_deletion_v2("Please remove records tied to both email addresses."))

    def test_check_failed_deletion(self) -> None:
        self.assertTrue(authorizes_deletion_v2("Please inspect our systems and tell me whether identifiable data has been removed."))

    def test_confirm_profile_is_gone(self) -> None:
        self.assertTrue(authorizes_deletion_v2("Could you confirm that my profile is gone?"))

    def test_closed_account_request(self) -> None:
        self.assertTrue(authorizes_deletion_v2("I would like it closed."))

    def test_existing_multilingual_intent_preserved(self) -> None:
        self.assertTrue(authorizes_deletion_v2("Por favor, eliminar mi cuenta"))
        self.assertTrue(authorizes_deletion_v2("アカウントを削除して"))

    def test_negative_request_does_not_authorize(self) -> None:
        for msg in (
            "Do not delete the profile; just correct my email.",
            "Please export my data without deleting my account.",
            "I want to cancel my deletion request.",
            "Please keep my account; update the phone number.",
            "I am not requesting deletion.",
        ):
            with self.subTest(msg=msg):
                self.assertFalse(authorizes_deletion_v2(msg))

    def test_unrelated_support_does_not_authorize(self) -> None:
        self.assertFalse(authorizes_deletion_v2("Please update the shipping address on my order."))
        self.assertFalse(authorizes_deletion_v2("Could you explain your privacy policy?"))

    def test_audit_request_changes_only_intent_gate(self) -> None:
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        text = "Please remove records under my previous email."
        req = DeletionRequest("synthetic_intent", "synth@example.com", start, "NOT_STARTED", start,
                              start + timedelta(days=3), text)
        identity = ResolvedIdentity("user_42", "synth@example.com", ("synth@example.com",), (), (), (), "snapshot")
        evidence = EvidenceIndex((Observation("evidence_42", start + timedelta(hours=5),
                                              "account_42", "account_service", "USER_PROFILE", "PRESENT",
                                              ("user_42",), "synthetic"),), (), ("evidence_42",))
        rules = ({"data_category": "USER_PROFILE", "condition": "Always", "required_action": "DELETE"},)
        old = plan_case(req, identity, evidence, rules, variant="latest")
        new = plan_case(req, identity, evidence, rules, variant="intent-v2")
        self.assertEqual(old.status, "NOT_APPLICABLE")
        self.assertEqual(new.status, "INCOMPLETE")
        self.assertEqual([s["operation"] for s in new.steps], [
            "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"])
        validate_prediction(new.status, new.steps, evidence_ids=frozenset({"evidence_42"}))


if __name__ == "__main__":
    unittest.main()
