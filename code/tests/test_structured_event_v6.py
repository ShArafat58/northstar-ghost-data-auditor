"""Synthetic tests for trace-safe JSONL readbacks and future-evidence gating.

No labelled sample answers or dataset-specific identities are used.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline import plan_case
from identity import ResolvedIdentity
from loaders import DeletionRequest, EvidenceFile
from observations import Observation, EvidenceIndex, build_index
from validator import validate_prediction

AT = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
IDENTITY = ResolvedIdentity("user_91", "alpha@example.com", ("alpha@example.com",), (), (), (), "snapshot")
RULES = (
    {"data_category": "USER_PROFILE", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "SEARCH_INDEX_ENTRY", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "SECONDARY_IDENTIFIER", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "MARKETING_PROFILE", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "SUPPORT_MESSAGE", "condition": "Contains shared context", "required_action": "REDACT"},
)


def request() -> DeletionRequest:
    return DeletionRequest("case_synthetic", "alpha@example.com", AT - timedelta(days=3),
                           "COMPLETE", AT, AT, "Delete all my account data")


class StructuredEventV6Tests(unittest.TestCase):
    def _index(self, lines: list[str], *, capture: datetime = AT, options: bool = True) -> EvidenceIndex:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fp = Path(tmp.name) / "events.log"
        fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return build_index([EvidenceFile("evidence_synth", fp, "synthetic", capture)],
                           enhanced_readback=True, structured_event_v6=options)

    def test_json_line_trace_resolves_404_resource(self):
        lines = [
            '2026-09-15T09:00:00Z {"trace_id":"run-1","event":"profile_lookup_started","lookup_key":"alpha@example.com"}',
            '2026-09-15T09:00:02Z {"trace_id":"run-1","resource_id":"account_404","event":"profile_lookup_completed","rows":"0","http_status":"404"}',
        ]
        idx = self._index(lines)
        old = self._index(lines, options=False)
        self.assertEqual(len(old.observations), 0)
        self.assertEqual(len(idx.observations), 1)
        self.assertEqual(idx.observations[0].state, "ABSENT")
        self.assertIn("alpha@example.com", idx.observations[0].identifiers)
        plan = plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6")
        self.assertEqual(plan.steps[0]["operation"], "VERIFY_RECORD_ABSENT")
        validate_prediction(plan.status, plan.steps, evidence_ids=frozenset({"evidence_synth"}))

    def test_trace_id_isolation(self):
        lines = [
            '2026-09-15T09:00:00Z {"trace_id":"run-A","event":"profile_lookup_started","lookup_key":"alpha@example.com"}',
            '2026-09-15T09:00:01Z {"trace_id":"run-B","resource_id":"account_12","event":"profile_lookup_completed","rows":"0","http_status":"404"}',
        ]
        idx = self._index(lines)
        self.assertNotIn("alpha@example.com", idx.observations[0].identifiers)
        self.assertEqual(plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6").steps, [])

    def test_started_after_completed_cannot_retroactively_link(self):
        lines = [
            '2026-09-15T09:00:00Z {"trace_id":"run-A","resource_id":"account_12","event":"profile_lookup_completed","rows":"0","http_status":"404"}',
            '2026-09-15T09:00:02Z {"trace_id":"run-A","event":"profile_lookup_started","lookup_key":"alpha@example.com"}',
        ]
        idx = self._index(lines)
        self.assertFalse(idx.observations[0].identifiers)

    def test_backup_risk_requires_true_eligibility_and_missing_block(self):
        lines = [
            '2026-09-15T09:00:00Z event=restore_policy_check_started trace_id=b1 subject_key="alpha@example.com"',
            '2026-09-15T09:00:01Z event=restore_candidate_evaluated trace_id=b1 resource_id=backup_12 eligible=true deletion_block=false',
        ]
        idx = self._index(lines)
        pred = plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6")
        self.assertEqual(pred.status, "INCOMPLETE")
        self.assertEqual([x["operation"] for x in pred.steps],
                         ["REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"])
        self.assertEqual(pred.steps[0]["evidence_id"], "evidence_synth")

    def test_blocked_or_ineligible_backup_is_not_active_risk(self):
        for eligible, block in [("false", "false"), ("true", "true")]:
            with self.subTest(eligible=eligible, block=block):
                idx = self._index([
                    '2026-09-15T09:00:00Z event=restore_policy_check_started trace_id=b1 subject_key="alpha@example.com"',
                    f'2026-09-15T09:00:01Z event=restore_candidate_evaluated trace_id=b1 resource_id=backup_12 eligible={eligible} deletion_block={block}',
                ])
                self.assertFalse(idx.observations)

    def test_legacy_contact_index_is_deletable_identity_record(self):
        idx = self._index([
            '2026-09-15T09:00:00Z event=contact_query_started trace_id=x1 lookup_key=alpha@example.com source=legacy_contact_index',
            '2026-09-15T09:00:01Z event=contact_query_completed trace_id=x1 resource_id=support_12 rows=1',
        ])
        obs = idx.observations[-1]
        self.assertEqual(obs.category, "SECONDARY_IDENTIFIER")
        self.assertEqual(obs.system_id, "support_platform")
        plan = plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6")
        self.assertEqual(plan.steps[1]["operation"], "DELETE_RECORD")

    def test_marketing_contact_is_deleted_not_generic_analytics(self):
        idx = self._index([
            '2026-09-15T09:00:00Z event=contact_lookup_started trace_id=x1 lookup_key=alpha@example.com',
            '2026-09-15T09:00:01Z event=contact_lookup_completed trace_id=x1 resource_id=analytics_55 rows=1 email_opt_in=true',
        ])
        self.assertEqual(idx.observations[-1].category, "MARKETING_PROFILE")
        pred = plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6")
        self.assertEqual(pred.steps[1]["operation"], "DELETE_RECORD")

    def test_future_export_does_not_cite_findings(self):
        future = AT + timedelta(days=1)
        current = self._index([
            '2026-09-15T10:00:00Z event=restore_policy_check_started trace_id=b1 subject_key=alpha@example.com',
            '2026-09-15T10:00:01Z event=restore_candidate_evaluated trace_id=b1 resource_id=backup_21 eligible=true deletion_block=false',
        ], capture=AT)
        later = self._index([
            '2026-09-15T11:00:00Z {"trace_id":"other","resource_id":"backup_21","event":"restore_candidate_evaluated","eligible":"true","deletion_block":"false","subject_key":"alpha@example.com"}',
        ], capture=future)
        joined = EvidenceIndex(current.observations + later.observations, (), ("evidence_synth", "future"), (),
                               {"evidence_synth": AT, "future": future})
        pred = plan_case(request(), IDENTITY, joined, RULES, variant="structured-event-v6")
        self.assertEqual(pred.steps[0]["evidence_id"], "evidence_synth")

    def test_typed_foreign_key_generates_planned_check_only_with_finding(self):
        subject_search = Observation("e", AT, "search_3", "search_service", "SEARCH_INDEX_ENTRY", "PRESENT",
                                     ("alpha@example.com",), "synthetic", linked_resources=("account_88",))
        idx = EvidenceIndex((subject_search,), (), ("e",), (), {"e": AT})
        pred = plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6")
        self.assertEqual(pred.status, "INCOMPLETE")
        self.assertEqual([x["resource_id"] for x in pred.steps if x["phase"] == "VERIFICATION"], ["account_88", "search_3"])
        self.assertEqual([x["evidence_id"] for x in pred.steps if x["phase"] == "VERIFICATION"], ["", ""])
        absent = Observation("e", AT, "search_3", "search_service", "SEARCH_INDEX_ENTRY", "ABSENT",
                             ("alpha@example.com",), "synthetic", linked_resources=("account_88",))
        nothing = EvidenceIndex((absent,), (), ("e",), (), {"e": AT})
        complete = plan_case(request(), IDENTITY, nothing, RULES, variant="structured-event-v6")
        self.assertEqual([x["resource_id"] for x in complete.steps], ["search_3"])

    def test_future_only_absence_not_complete_verification_proof(self):
        later = AT + timedelta(days=1)
        row = Observation("future", AT - timedelta(minutes=1), "account_31",
                          "account_service", "USER_PROFILE", "ABSENT",
                          ("alpha@example.com",), "future-exported")
        idx = EvidenceIndex((row,), (), ("future",), (), {"future": later})
        prediction = plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6")
        self.assertEqual(prediction.steps, [])
        self.assertEqual(prediction.status, "COMPLETE")
        # Empty-proof COMPLETE remains a known coverage limitation; it does
        # not falsely cite an unavailable audit-time source.

    def test_pre_request_absent_does_not_count_as_post_request_proof(self):
        past = AT - timedelta(days=10)
        old = Observation("e", past, "account_44", "account_service", "USER_PROFILE", "ABSENT",
                          ("alpha@example.com",), "synthetic")
        idx = EvidenceIndex((old,), (), ("e",), (), {"e": past})
        self.assertEqual(plan_case(request(), IDENTITY, idx, RULES, variant="structured-event-v6").steps, [])


if __name__ == "__main__":
    unittest.main()
