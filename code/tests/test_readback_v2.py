"""A/B regression for direct log readback observations, using synthetic facts."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loaders import EvidenceFile, DeletionRequest
from identity import ResolvedIdentity
from observations import build_index, scan_text
from baseline import plan_case
from validator import validate_prediction

T = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


class ReadbackV2Tests(unittest.TestCase):
    def evidence(self, lines: str) -> tuple[tempfile.TemporaryDirectory, EvidenceFile]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / 'readback.log'
        path.write_text(lines, encoding='utf-8')
        return directory, EvidenceFile('evidence_test', path, 'test', T)

    def test_identifiable_analytics_is_proof_of_remaining_pii(self):
        directory, file = self.evidence('''2026-09-15T10:00:00Z service=analytics_platform trace_id=r1 event=analytics_profile_read_started resource_id=analytics_121 lookup_key=user_71
2026-09-15T10:00:01Z service=analytics_platform trace_id=r1 event=analytics_profile_read_completed resource_id=analytics_121 customer_reference=null email=alice@example.com phone=null email_hash=null audience_cohort=shopper
''')
        try:
            self.assertEqual(len(scan_text(file)), 0)
            index = build_index([file], enhanced_readback=True)
            self.assertEqual(len(index.observations), 1)
            o = index.observations[0]
            self.assertEqual(o.category, 'ANALYTICS_PROFILE')
            self.assertEqual(o.state, 'PRESENT')
            self.assertIn('user_71', o.identifiers)
        finally:
            directory.cleanup()

    def test_anonymized_readback_uses_traced_identity(self):
        directory, file = self.evidence('''2026-09-15T10:00:00Z service=analytics_platform trace_id=r2 event=analytics_profile_read_started resource_id=analytics_124 lookup_key=alice@example.com
2026-09-15T10:00:01Z service=analytics_platform trace_id=r2 event=analytics_profile_read_completed resource_id=analytics_124 customer_reference=null email=null phone=null email_hash=null audience_cohort=shopper
''')
        try:
            observations = scan_text(file, enhanced_readback=True)
            self.assertEqual([o.state for o in observations], ['ANONYMIZED'])
            self.assertIn('alice@example.com', observations[0].identifiers)
        finally:
            directory.cleanup()

    def test_support_readback_handles_redacted_and_identifiable(self):
        directory, file = self.evidence('''2026-09-15T10:00:00Z service=support_platform trace_id=s1 event=support_ticket_read_started resource_id=support_302 lookup_key=alice@example.com
2026-09-15T10:00:01Z service=support_platform trace_id=s1 event=support_ticket_read_completed resource_id=support_302 customer_reference=null requester_name=null requester_email=null requester_phone=null
2026-09-15T10:00:02Z service=support_platform trace_id=s2 event=support_ticket_read_started resource_id=support_303 lookup_key=alice@example.com
2026-09-15T10:00:03Z service=support_platform trace_id=s2 event=support_ticket_read_completed resource_id=support_303 customer_reference=null requester_name=null requester_email=alice@example.com requester_phone=null
''')
        try:
            self.assertEqual([(o.resource_id, o.state) for o in scan_text(file, enhanced_readback=True)], [('support_302', 'REDACTED'), ('support_303', 'PRESENT')])
        finally:
            directory.cleanup()

    def test_no_guess_for_incomplete_payload_or_workflow_result(self):
        directory, file = self.evidence('''2026-09-15T10:00:00Z service=support_platform trace_id=s3 event=support_ticket_read_completed resource_id=support_304 customer_reference=user_71 message_excerpt=order
2026-09-15T10:00:01Z service=support_platform event=deletion_task_completed resource_id=support_305 requester_email=alice@example.com
''')
        try:
            self.assertEqual(scan_text(file, enhanced_readback=True), [])
        finally:
            directory.cleanup()

    def test_readback_passes_planner_and_contract_validation(self):
        directory, file = self.evidence('''2026-09-15T10:00:00Z service=analytics_platform trace_id=z1 event=analytics_profile_read_started resource_id=analytics_777 lookup_key=user_71
2026-09-15T10:00:01Z service=analytics_platform trace_id=z1 event=analytics_profile_read_completed resource_id=analytics_777 customer_reference=null email=alice@example.com phone=null email_hash=null
''')
        try:
            index = build_index([file], enhanced_readback=True)
            req = DeletionRequest('synthetic_case', 'alice@example.com', T-timedelta(days=5), 'COMPLETE', T-timedelta(days=2), T, 'Please remove my records.')
            identity = ResolvedIdentity('user_71', 'alice@example.com', ('alice@example.com',), (), (), (), 'snapshot')
            rules = ({'data_category':'ANALYTICS_PROFILE', 'condition':'record still attributable', 'required_action':'ANONYMIZE'},)
            plan = plan_case(req, identity, index, rules, variant='readback-v2')
            self.assertEqual(plan.status, 'INCOMPLETE')
            self.assertEqual([s['operation'] for s in plan.steps], ['REPORT_EXPOSED_PII','ANONYMIZE_RECORD','VERIFY_RECORD_ANONYMIZED'])
            validate_prediction(plan.status, plan.steps, evidence_ids=frozenset({'evidence_test'}))
        finally:
            directory.cleanup()


if __name__ == '__main__':
    unittest.main()
