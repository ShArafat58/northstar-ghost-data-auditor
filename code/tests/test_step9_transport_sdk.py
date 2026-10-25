"""Offline SDK transport and sanitized-error tests; never perform network calls."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_step9
import groq_client


class FakeStatusError(Exception):
    def __init__(self, status, body):
        self.status_code = status
        self.body = body


class FakeConnError(Exception):
    pass


class FakeGroq:
    instances = []

    def __init__(self, **kwargs):
        self.options = kwargs
        self.models = self
        self.chat = self
        self.completions = self
        self.calls = []
        self.error = None
        self.__class__.instances.append(self)

    def list(self):
        return type('List', (), {'data':[type('Model', (), {'id':'openai/gpt-oss-20b'})()]})()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return type('Completion', (), {'model_dump': lambda _, **kw: {
            'choices':[{'message':{'tool_calls':[]}}],
            'usage':{'prompt_tokens':12,'completion_tokens':8}
        }})()


class GroqTransportTests(unittest.TestCase):
    def setUp(self):
        FakeGroq.instances.clear()
        self.fake_modules = (FakeGroq, FakeConnError, FakeStatusError)
        self.patcher = patch.object(agent_step9, 'require_groq_sdk', return_value=self.fake_modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_hidden_reasoning_and_sdk_model(self):
        client = agent_step9.GroqHTTPClient(api_key='testing-secret', delay=0)
        out = client.completion([{'role':'user','content':'test'}], [], 'required')
        self.assertIn('choices', out)
        self.assertEqual(FakeGroq.instances[-1].calls[-1]['reasoning_format'], 'hidden')
        self.assertEqual(FakeGroq.instances[-1].calls[-1]['reasoning_effort'], 'low')
        self.assertEqual(FakeGroq.instances[-1].options['max_retries'], 2)

    def test_403_permission_code_extracted_without_body(self):
        client = agent_step9.GroqHTTPClient(api_key='secret')
        FakeGroq.instances[-1].error = FakeStatusError(403, {'error': {
            'type':'permissions_error', 'code':'model_permission_blocked_project',
            'message':'contains-secret-content'}})
        with self.assertRaisesRegex(agent_step9.ProviderError, 'model_permission_blocked_project') as ctx:
            client.completion([], [], 'required')
        self.assertNotIn('contains-secret-content', str(ctx.exception))
        self.assertNotIn('secret', str(ctx.exception))

    def test_untrusted_error_code_is_not_echoed(self):
        exc = FakeStatusError(403, {'error': {'code':'token secret@example.com', 'message':'private'}})
        self.assertEqual(groq_client.safe_api_error(exc), 'HTTP 403')

    def test_sdk_credential_preflight(self):
        with patch.object(groq_client, 'require_groq_sdk', return_value=self.fake_modules), \
             patch.dict(os.environ, {'GROQ_API_KEY':'secret'}, clear=False):
            self.assertTrue(groq_client.check_groq_model())

    def test_sdk_missing_is_explicit(self):
        with patch.object(agent_step9, 'require_groq_sdk', side_effect=RuntimeError('pip install groq')):
            with self.assertRaisesRegex(agent_step9.ProviderError, 'pip install groq'):
                agent_step9.GroqHTTPClient(api_key='secret')

if __name__ == '__main__':
    unittest.main()
