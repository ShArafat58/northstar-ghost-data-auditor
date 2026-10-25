"""Synthetic, label-independent verification recall tests for the V3 experiment."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline import plan_case
from identity import ResolvedIdentity
from loaders import DeletionRequest, EvidenceFile
from observations import (
    EvidenceIndex, Observation, _scan_media_text, from_record, linked_resources_of,
)
from validator import validate_prediction

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
SUBJECT = ResolvedIdentity("user_73", "alice@example.com", ("alice@example.com",), (), (), (), "snapshot")
RULES = (
    {"data_category": "USER_PROFILE", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "SEARCH_INDEX_ENTRY", "condition": "Always", "required_action": "DELETE"},
)


def request() -> DeletionRequest:
    return DeletionRequest("synthetic", "alice@example.com", NOW - timedelta(days=8),
                           "COMPLETE", NOW - timedelta(days=5), NOW, "Please delete my account")


def obs(resource: str, state: str, keys: tuple[str, ...], *,
        when: datetime = NOW - timedelta(days=1),
        linked: tuple[str, ...] = ()) -> Observation:
    system = "search_service" if resource.startswith("search_") else "account_service"
    category = "SEARCH_INDEX_ENTRY" if resource.startswith("search_") else "USER_PROFILE"
    return Observation("evidence_test", when, resource, system, category, state,
                       keys, "synthetic-readback", linked_resources=linked)


class VerificationV3Tests(unittest.TestCase):
    def test_exact_typed_resource_link_recovers_absent_verification(self):
        index = EvidenceIndex((
            obs("search_271", "PRESENT", ("user_73",), linked=("account_973",)),
            obs("account_973", "ABSENT", ()),
        ), (), ("evidence_test",))
        control = plan_case(request(), SUBJECT, index, RULES, variant="readback-v2")
        enriched = plan_case(request(), SUBJECT, index, RULES, variant="verification-v3")
        self.assertNotIn("account_973", [s["resource_id"] for s in control.steps])
        self.assertIn("account_973", [s["resource_id"] for s in enriched.steps])
        self.assertEqual(enriched.status, "INCOMPLETE")
        self.assertIn("VERIFY_RECORD_ABSENT", [s["operation"] for s in enriched.steps])
        validate_prediction(enriched.status, enriched.steps, evidence_ids=frozenset({"evidence_test"}))

    def test_future_link_does_not_leak(self):
        index = EvidenceIndex((
            obs("search_271", "PRESENT", ("user_73",), linked=("account_973",), when=NOW + timedelta(hours=1)),
            obs("account_973", "ABSENT", ()),
        ), (), ("evidence_test",))
        planned = plan_case(request(), SUBJECT, index, RULES, variant="verification-v3")
        self.assertEqual(planned.steps, [])

    def test_same_file_without_explicit_link_does_not_expand(self):
        index = EvidenceIndex((
            obs("search_271", "PRESENT", ("user_73",)),
            obs("account_973", "ABSENT", ()),
        ), (), ("evidence_test",))
        enriched = plan_case(request(), SUBJECT, index, RULES, variant="verification-v3")
        self.assertNotIn("account_973", [s["resource_id"] for s in enriched.steps])

    def test_extract_foreign_key_only_not_generic_text(self):
        file = EvidenceFile("evidence_test", Path("unused.json"), "test", NOW)
        row = {"resource_id": "search_271", "email": "alice@example.com", "source_resource_id": "account_973",
               "status": "FOUND", "message_excerpt": "link to account_666"}
        result = from_record(row, file)
        self.assertIsNotNone(result)
        self.assertEqual(result.linked_resources, ("account_973",))
        self.assertEqual(linked_resources_of({"source_resource_id": "garbage", "linked_resource_id": "account_41"}), ("account_41",))

    def test_multiline_status_block_requires_identity(self):
        file = EvidenceFile("evidence_test", Path("unused.pdf"), "test", NOW)
        content = "resource_id: account_973\nlookup_key: alice@example.com\nstatus: NOT_FOUND"
        self.assertEqual(_scan_media_text(file, content), [])
        results = _scan_media_text(file, content, enriched=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].state, "ABSENT")
        self.assertIn("alice@example.com", results[0].identifiers)

    def test_multiline_block_rejects_ambiguous_resource_or_unverified_status(self):
        file = EvidenceFile("evidence_test", Path("unused.pdf"), "test", NOW)
        ambiguous = "resource_id: account_973\nresource_id: account_974\nemail: alice@example.com\nstatus: NOT_FOUND"
        narrative = "resource_id: account_973\nemail: alice@example.com\nI heard account not found last year"
        self.assertEqual(_scan_media_text(file, ambiguous, enriched=True), [])
        self.assertEqual(_scan_media_text(file, narrative, enriched=True), [])


if __name__ == "__main__":
    unittest.main()
