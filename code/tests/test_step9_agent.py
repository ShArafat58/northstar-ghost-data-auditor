"""Offline Groq tool-agent tests, no network or credentials required."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_step9 import CaseTools, Usage, AgentResult, run_case_agent
from baseline import CasePlan
from loaders import DeletionRequest
from observations import EvidenceIndex, Observation
from identity import ResolvedIdentity
from validator import validate_prediction

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)
RULES = (
    {'rule_id': 'r1', 'data_category': 'USER_PROFILE', 'condition': 'Always', 'required_action': 'DELETE'},
    {'rule_id': 'r2', 'data_category': 'SECURITY_AUDIT_LOG', 'condition': 'within retention', 'required_action': 'RETAIN'},
)

def step(phase='VERIFICATION', op='VERIFY_RECORD_ABSENT', state='ABSENT', evidence='evidence_01'):
    return {'step_no': 1, 'phase': phase, 'system_id': 'account_service',
            'resource_id': 'account_01', 'operation': op, 'state': state, 'evidence_id': evidence}


def make_tools(status='COMPLETE', steps=None, observation_state='ABSENT'):
    r = DeletionRequest('sample_xyz', 'person@example.com', NOW, 'COMPLETE', NOW, NOW,
                        'Please delete my account')
    i = ResolvedIdentity('user_01', 'person@example.com', ('person@example.com',), (), (), (), 'snapshot')
    obs = Observation('evidence_01', NOW, 'account_01', 'account_service', 'USER_PROFILE',
                      observation_state, ('user_01',), 'test')
    index = EvidenceIndex((obs,), (), ('evidence_01',), (), {'evidence_01': NOW})
    plan = CasePlan(r.case_id, status, [] if steps is None else steps, (), 1)
    return CaseTools(r, i, index, RULES, plan, frozenset({'evidence_01'}))


def reply(name, arguments, usage=20):
    return {'choices': [{'message': {'role': 'assistant', 'tool_calls': [
              {'id': 'call_' + name, 'type': 'function', 'function': {
               'name': name, 'arguments': json.dumps(arguments)}}]}}],
            'usage': {'prompt_tokens': usage, 'completion_tokens': 10}}


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []
    def completion(self, messages, tools, tool_choice):
        self.seen.append((messages, tools, tool_choice))
        return self.replies.pop(0)


class Step9Tests(unittest.TestCase):
    def test_real_tool_protocol_requires_dossier_then_submission(self):
        tools = make_tools(steps=[step()])
        client = FakeClient(reply('get_case_dossier', {}), reply('submit_audit', {
            'deletion_status': 'COMPLETE', 'steps': [step()]}))
        usage = Usage()
        result = run_case_agent(tools, client, usage)
        self.assertEqual(result.source, 'groq_tool_agent')
        self.assertEqual(usage.calls, 2)
        self.assertEqual(usage.input_tokens, 40)
        self.assertEqual(usage.output_tokens, 20)
        self.assertEqual(usage.cases_with_model, 1)
        self.assertEqual(usage.tool_calls['get_case_dossier'], 1)
        self.assertEqual(usage.tool_calls['submit_audit'], 1)
        self.assertEqual(client.seen[0][2], 'required')
        self.assertEqual(len(client.seen[0][1]), 1)
        self.assertIn('role', client.seen[1][0][-1])
        self.assertEqual(client.seen[1][0][-1]['role'], 'tool')

    def test_compact_tool_requires_dossier_first(self):
        tools = make_tools(steps=[step()])
        self.assertFalse(tools.dispatch('confirm_grounded_draft', {})['accepted'])

    def test_compact_tool_confirmation_accepts_only_original_grounded_draft(self):
        tools = make_tools(steps=[step()])
        usage = Usage()
        client = FakeClient(reply('get_case_dossier', {}),
                            reply('confirm_grounded_draft', {}))
        result = run_case_agent(tools, client, usage)
        self.assertEqual(result.source, 'groq_tool_agent')
        self.assertEqual(result.reason, 'confirmed_grounded_draft_tool_recovery')
        self.assertEqual(result.plan.steps, [step()])
        self.assertEqual(usage.tool_calls['confirm_grounded_draft'], 1)
        self.assertEqual(usage.cases_with_model, 1)
        self.assertEqual(usage.fallback_cases, 0)
        self.assertEqual(usage.calls, 2)

    def test_compact_tool_rejects_any_extra_arguments(self):
        tools = make_tools(steps=[step()]); tools.get_case_dossier()
        self.assertIn('error', tools.dispatch('confirm_grounded_draft', {'fabricated': 'yes'}))
        self.assertIsNone(tools.accepted)

    def test_dossier_contains_original_case_but_not_sample_labels(self):
        text = json.dumps(make_tools(steps=[step()]).get_case_dossier())
        self.assertIn('person@example.com', text)
        self.assertNotIn('sample_output', text)

    def test_cannot_remove_baseline_steps(self):
        tools = make_tools(steps=[step()]); tools.get_case_dossier()
        self.assertFalse(tools.submit_audit('COMPLETE', [])['accepted'])

    def test_cannot_fabricate_citation(self):
        tools = make_tools(steps=[]); tools.get_case_dossier()
        added = step(); added['evidence_id'] = 'made_up'
        self.assertFalse(tools.submit_audit('COMPLETE', [added])['accepted'])

    def test_cannot_change_baseline_status(self):
        tools = make_tools(steps=[step()]); tools.get_case_dossier()
        self.assertFalse(tools.submit_audit('INCOMPLETE', [step()])['accepted'])

    def test_cannot_add_unlinked_verification(self):
        tools = make_tools(steps=[]); tools.get_case_dossier()
        unknown = step(); unknown['resource_id'] = 'account_999'
        self.assertFalse(tools.submit_audit('COMPLETE', [unknown])['accepted'])

    def test_added_observed_verification_allowed(self):
        tools = make_tools(steps=[]); tools.get_case_dossier()
        result = tools.submit_audit('COMPLETE', [step()])
        self.assertTrue(result['accepted'])

    def test_future_capture_not_proof(self):
        tools = make_tools(steps=[]); tools.get_case_dossier()
        from datetime import timedelta
        tools.index.file_captures['evidence_01'] = NOW + timedelta(days=1)
        self.assertFalse(tools.submit_audit('COMPLETE', [step()])['accepted'])

    def test_wrong_policy_verification_rejected(self):
        tools = make_tools(steps=[], observation_state='ANONYMIZED'); tools.get_case_dossier()
        x=step(op='VERIFY_RECORD_ANONYMIZED', state='ANONYMIZED')
        self.assertFalse(tools.submit_audit('COMPLETE', [x])['accepted'])

    def test_incomplete_invalid_payload_safe_fallback(self):
        find = step(phase='FINDING', op='REPORT_REMAINING_RECORD', state='PRESENT')
        tools = make_tools(status='INCOMPLETE', steps=[find], observation_state='PRESENT')
        usage = Usage()
        client = FakeClient(reply('get_case_dossier', {}), reply('submit_audit', {
            'deletion_status': 'INCOMPLETE', 'steps': []}),
            {'choices': [{'message': {'role': 'assistant', 'content': 'not finished'}}], 'usage': {}})
        result = run_case_agent(tools, client, usage)
        self.assertEqual(result.source, 'v6_safety_fallback')
        self.assertEqual(result.plan.steps, [find])
        self.assertEqual(usage.rejected_submissions, 1)
        self.assertEqual(usage.fallback_cases, 1)

    def test_wrong_initial_tool_fallback(self):
        tools = make_tools(steps=[step()]); usage = Usage()
        client = FakeClient(reply('submit_audit', {'deletion_status': 'COMPLETE', 'steps': [step()]}))
        result = run_case_agent(tools, client, usage)
        self.assertEqual(result.source, 'v6_safety_fallback')
        self.assertEqual(usage.reasons['dossier_not_called'], 1)

    def test_duplicate_steps_rejected(self):
        tools = make_tools(steps=[step()]); tools.get_case_dossier()
        s1, s2 = step(), step(); s2['step_no'] = 2
        self.assertFalse(tools.submit_audit('COMPLETE', [s1,s2])['accepted'])

    def test_unrelated_resource_history_does_not_leak(self):
        tools = make_tools()
        self.assertIn('error', tools.get_resource_history('account_500'))

    def test_usage_aggregation(self):
        usage = Usage()
        usage.record({'usage': {'prompt_tokens': 12, 'completion_tokens': 6}})
        usage.add_tool('get_case_dossier')
        usage.fallback('rate_limited')
        self.assertEqual(usage.as_dict()['calls'], 1)
        self.assertEqual(usage.as_dict()['reasons']['rate_limited'], 1)

    def test_validator_constraints(self):
        validate_prediction('COMPLETE', [step()], evidence_ids=frozenset({'evidence_01'}))
        with self.assertRaises(ValueError):
            validate_prediction('INCOMPLETE', [step()], evidence_ids=frozenset({'evidence_01'}))

if __name__=='__main__':
    unittest.main()
