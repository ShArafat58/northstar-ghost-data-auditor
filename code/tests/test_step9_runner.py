"""Step 9 runner end-to-end tests with a model emulation, never network."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as runner


class LocalFakeGroq:
    def __init__(self):
        self.call_count = 0

    def completion(self, messages, tools, tool_choice):
        self.call_count += 1
        if len(tools) == 1:
            name, args = 'get_case_dossier', {}
        else:
            dossier = next(json.loads(m['content']) for m in messages
                           if m.get('role') == 'tool' and m.get('name') == 'get_case_dossier')
            name, args = 'submit_audit', dossier['conservative_grounded_draft']
        return {'choices': [{'message': {'role': 'assistant', 'tool_calls': [
            {'id': 'call_' + str(self.call_count), 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(args)}}]}}],
            'usage': {'prompt_tokens': 25, 'completion_tokens': 12}}


class Step9RunnerTests(unittest.TestCase):
    def test_auto_with_injected_provider_uses_real_tool_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            out, checkpoint, usage = (Path(d) / x for x in ('output.csv', 'checkpoint.jsonl', 'usage.md'))
            fake = LocalFakeGroq()
            args = ['--mode', 'agent', '--sample', '--limit', '2', '--out', str(out),
                    '--checkpoint', str(checkpoint), '--usage-report', str(usage), '--delay', '0']
            self.assertEqual(runner.main(args, client_override=fake), 0)
            self.assertEqual(fake.call_count, 4)
            with out.open(newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(list(rows[0]), ['case_id','deletion_status','steps_json'])
            report = usage.read_text(encoding='utf-8')
            self.assertIn('Cases with accepted model tool submission: **2**', report)
            self.assertIn('Model completions: **4**', report)
            self.assertIn('Prompt/input tokens: **100**', report)
            self.assertIn('Completion/output tokens: **48**', report)
            self.assertNotIn('person@example.com', report)
            self.assertEqual(runner.main(args + ['--resume'], client_override=fake), 0)
            self.assertEqual(fake.call_count, 4)

    def test_full_output_rejects_partial_518_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(runner.__file__).resolve().parents[1]
            with self.assertRaises(ValueError):
                runner.main(['--mode','baseline','--sample','--limit','1','--out',str(root/'output.csv')])

    def test_usage_math_is_based_on_actual_tokens(self):
        from agent_step9 import Usage
        u=Usage(input_tokens=1_000_000, output_tokens=1_000_000, calls=2)
        text=runner.usage_markdown(u, selected=2, baseline_rows=0, agent_rows=2,
                                   mode='agent', path=Path('foo.csv'), sample=True, completed=True)
        self.assertIn('$0.375000', text)
        self.assertIn('Avg total tokens per selected request: **1000000.00**', text)

    def test_resume_refuses_changed_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ck.jsonl'
            path.write_text('{"type":"header","fingerprint":"other"}\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                runner._existing_checkpoint(path,'new')

    def test_case_ids_sorted_and_invalid_multiplicity_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/'output.csv'
            runner._write_csv(out, {'b': ('NOT_APPLICABLE', []), 'a': ('COMPLETE', [])},
                              expected_case_ids=['b','a'])
            with out.open(newline='') as f:
                self.assertEqual([r['case_id'] for r in csv.DictReader(f)], ['a','b'])
            with self.assertRaises(ValueError):
                runner._write_csv(out, {'a': ('COMPLETE', [])}, expected_case_ids=['a','b'])

if __name__ == '__main__':
    unittest.main()
