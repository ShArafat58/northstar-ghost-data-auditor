"""Verify bounded recovery from Groq 400 tool_use_failed without network or secrets."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_step9


class FakeStatusError(Exception):
    def __init__(self, status: int, code: str):
        self.status_code = status
        self.body = {'error': {'type': 'invalid_request_error', 'code': code,
                               'message': 'SENSITIVE_PROVIDER_BODY_DO_NOT_LOG'}}


class FakeConnError(Exception):
    pass


class FakeGroq:
    instances: list = []

    def __init__(self, **kw):
        self.calls = []
        self.failures = []
        self.chat = self
        self.completions = self
        self.__class__.instances.append(self)

    def create(self, **kw):
        self.calls.append(kw)
        if self.failures:
            problem = self.failures.pop(0)
            if problem is not None:
                raise problem
        return type('Response', (), {'model_dump': lambda _, **k: {
            'choices': [{'message': {'tool_calls': []}}],
            'usage': {'prompt_tokens': 7, 'completion_tokens': 5},
        }})()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        FakeGroq.instances.clear()
        p = patch.object(agent_step9, 'require_groq_sdk', return_value=(FakeGroq, FakeConnError, FakeStatusError))
        p.start()
        self.addCleanup(p.stop)
        self.client = agent_step9.GroqHTTPClient(api_key='TEST_SECRET', attempts=1, delay=0)
        self.transport = FakeGroq.instances[-1]
        self.messages = [{'role':'system', 'content':'test'}, {'role':'user','content':'audit'}]

    def test_first_dossier_400_recovers_with_forced_dossier(self):
        self.transport.failures = [FakeStatusError(400, 'tool_use_failed')]
        result = self.client.completion(self.messages, [agent_step9.DOSSIER_TOOL], 'required')
        self.assertEqual(result['usage']['prompt_tokens'], 7)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.transport.calls[1]['tool_choice']['function']['name'], 'get_case_dossier')
        self.assertEqual(self.transport.calls[1]['tools'][0]['function']['name'], 'get_case_dossier')
        self.assertEqual(self.transport.calls[1]['temperature'], 0.1)
        self.assertEqual(self.messages, [{'role':'system', 'content':'test'}, {'role':'user','content':'audit'}])

    def test_later_tool_error_recovers_with_submit_audit_only(self):
        self.transport.failures = [FakeStatusError(400, 'tool_use_failed')]
        self.client.completion(self.messages, agent_step9.TOOLS, 'required')
        self.assertEqual(len(self.transport.calls), 2)
        retry = self.transport.calls[1]
        self.assertEqual([t['function']['name'] for t in retry['tools']], ['submit_audit'])
        self.assertEqual(retry['tool_choice']['function']['name'], 'submit_audit')
        self.assertIn('conservative_grounded_draft', retry['messages'][-1]['content'])

    def test_rate_limit_does_not_trigger_format_retry(self):
        self.transport.failures = [FakeStatusError(429, 'rate_limit_exceeded')]
        with self.assertRaisesRegex(agent_step9.ProviderError, 'HTTP 429') as ctx:
            self.client.completion(self.messages, agent_step9.TOOLS, 'required')
        self.assertEqual(len(self.transport.calls), 1)
        self.assertNotIn('SENSITIVE', str(ctx.exception))

    def test_invalid_key_does_not_trigger_format_retry(self):
        self.transport.failures = [FakeStatusError(401, 'invalid_api_key')]
        with self.assertRaisesRegex(agent_step9.ProviderError, 'HTTP 401'):
            self.client.completion(self.messages, agent_step9.TOOLS, 'required')
        self.assertEqual(len(self.transport.calls), 1)

    def test_other_bad_request_is_not_retried(self):
        self.transport.failures = [FakeStatusError(400, 'invalid_request')]
        with self.assertRaisesRegex(agent_step9.ProviderError, 'HTTP 400'):
            self.client.completion(self.messages, agent_step9.TOOLS, 'required')
        self.assertEqual(len(self.transport.calls), 1)

    def test_double_format_failure_uses_compact_confirmation(self):
        self.transport.failures = [FakeStatusError(400, 'tool_use_failed'),
                                   FakeStatusError(400, 'tool_use_failed')]
        result = self.client.completion(self.messages, agent_step9.TOOLS, 'required')
        self.assertIn('usage', result)
        self.assertEqual(len(self.transport.calls), 3)
        last = self.transport.calls[2]
        self.assertEqual([t['function']['name'] for t in last['tools']],
                         ['confirm_grounded_draft'])
        self.assertEqual(last['tool_choice']['function']['name'],
                         'confirm_grounded_draft')
        self.assertEqual(last['max_completion_tokens'], 256)
        self.assertEqual(last['temperature'], 0)
        self.assertIn('empty JSON object', last['messages'][-1]['content'])
        self.assertEqual(self.messages, [{'role':'system','content':'test'},
                                         {'role':'user','content':'audit'}])

    def test_dossier_format_recovery_remains_bounded_at_two_calls(self):
        self.transport.failures = [FakeStatusError(400, 'tool_use_failed'),
                                   FakeStatusError(400, 'tool_use_failed')]
        with self.assertRaisesRegex(agent_step9.ProviderError, 'retry rejected: HTTP 400'):
            self.client.completion(self.messages, [agent_step9.DOSSIER_TOOL], 'required')
        self.assertEqual(len(self.transport.calls), 2)

    def test_three_format_errors_are_bounded_and_sanitized(self):
        self.transport.failures = [FakeStatusError(400, 'tool_use_failed'),
                                   FakeStatusError(400, 'tool_use_failed'),
                                   FakeStatusError(400, 'tool_use_failed')]
        with self.assertRaisesRegex(agent_step9.ProviderError,
                                    'compact-tool confirmation rejected: HTTP 400') as ctx:
            self.client.completion(self.messages, agent_step9.TOOLS, 'required')
        self.assertEqual(len(self.transport.calls), 3)
        self.assertNotIn('SENSITIVE', str(ctx.exception))
        self.assertNotIn('TEST_SECRET', str(ctx.exception))

    def test_401_on_compact_attempt_not_retried(self):
        self.transport.failures = [FakeStatusError(400, 'tool_use_failed'),
                                   FakeStatusError(400, 'tool_use_failed'),
                                   FakeStatusError(401, 'invalid_api_key')]
        with self.assertRaisesRegex(agent_step9.ProviderError, 'HTTP 401'):
            self.client.completion(self.messages, agent_step9.TOOLS, 'required')
        self.assertEqual(len(self.transport.calls), 3)


if __name__ == '__main__':
    unittest.main()
