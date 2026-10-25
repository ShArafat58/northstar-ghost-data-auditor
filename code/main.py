"""Step 9: 518-case Groq tool agent with grounded V6 fallback and usage accounting.

Default auto mode: agent if GROQ_API_KEY exists; otherwise transparent V6 fallback.
The public sample_output.csv is never read by this file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from agent_step9 import (AgentResult, CaseTools, GroqHTTPClient, ProviderError,
                         Usage, run_case_agent)
from baseline import CasePlan, plan_case
from groq_client import DEFAULT_MODEL
from identity import IdentityResolver
from loaders import load_dataset
from observations import build_index
from validator import serialize_steps, validate_prediction

PRICING_USD_PER_M = {DEFAULT_MODEL: (0.075, 0.30)}  # Groq published list price; free tier can bill $0.


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('auto', 'agent', 'baseline'), default='auto',
                        help='auto=Groq if GROQ_API_KEY is set; otherwise explicit safe fallback')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--sample', action='store_true', help='32 public sample requests; never writes root output.csv')
    parser.add_argument('--limit', type=int, default=0, help='Optional small smoke run (requires --out)')
    parser.add_argument('--out', type=Path, help='Predictions CSV; default root output.csv for full run')
    parser.add_argument('--usage-report', type=Path, help='Markdown usage report path')
    parser.add_argument('--checkpoint', type=Path, help='Crash-safe JSONL progress log (defaults under code/evaluation)')
    parser.add_argument('--resume', action='store_true', help='Resume with matching fingerprint, mode and model')
    parser.add_argument('--max-rounds', type=int, default=5, help='Max model turns per case')
    parser.add_argument('--delay', type=float, default=1.0, help='Seconds before each Groq call; rate-limit protection')
    parser.add_argument('--max-agent-calls', type=int, default=1600,
                        help='Total allowed model API calls before V6 fallback; 0 means unlimited')
    parser.add_argument('--strict-agent', action='store_true',
                        help='Fail instead of silently falling back if any Groq case cannot be completed')
    return parser


def _input_fingerprint(dataset, mode: str, model: str, sample: bool, limit: int) -> str:
    # A stable request/evidence fingerprint is required before replaying a checkpoint.
    descriptor = {'mode': mode, 'model': model, 'sample': sample, 'limit': limit,
                  'requests': [(r.case_id, r.email, r.request_text,
                                r.requested_at.isoformat(), r.audit_at.isoformat()) for r in dataset.requests],
                  'evidence': [(e.file_id, e.path.name, e.captured_at.isoformat(), e.path.stat().st_size)
                               for e in dataset.evidence]}
    return hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode('utf-8')).hexdigest()


def _existing_checkpoint(path: Path, fingerprint: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open('r', encoding='utf-8') as f:
        entries = [json.loads(line) for line in f if line.strip()]
    if not entries or entries[0] != {'type': 'header', 'fingerprint': fingerprint}:
        raise ValueError('Checkpoint fingerprint mismatch; refusing unsafe resume')
    return entries[1:]


def _append_checkpoint(path: Path, record: dict[str, Any]) -> None:
    # No API requests or credentials are written to disk.
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, separators=(',', ':'), ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def _write_csv(path: Path, results: dict[str, tuple[str, list[dict[str, Any]]]],
               *, expected_case_ids: list[str]) -> None:
    if set(results) != set(expected_case_ids):
        raise ValueError('Prediction IDs do not match all target cases')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('case_id', 'deletion_status', 'steps_json'))
        for case_id in sorted(results):
            status, steps = results[case_id]
            writer.writerow((case_id, status, serialize_steps(steps)))
    os.replace(tmp, path)


def usage_markdown(usage: Usage, *, selected: int, baseline_rows: int, agent_rows: int,
                   mode: str, path: Path, sample: bool, completed: bool) -> str:
    input_price, output_price = PRICING_USD_PER_M.get(usage.model, (0.0, 0.0))
    estimated = (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000
    avg_input = usage.input_tokens / selected if selected else 0.0
    avg_output = usage.output_tokens / selected if selected else 0.0
    fallback_reasons = ', '.join(f'{name}={n}' for name, n in sorted(usage.reasons.items())) or 'none'
    tool_usage = ', '.join(f'{name}={n}' for name, n in sorted(usage.tool_calls.items())) or 'none'
    return f'''# Step 9 Agent Usage Report

This report describes **real execution measurements**, not simulated or estimated token counts.

- Scope: {'32-case sample/smoke' if sample else '518-case production dataset'}
- Output: `{path.name}`
- Completion: {'all selected cases' if completed else 'partial run'}
- Requested mode: `{mode}`
- Model: `{usage.model}`
- Provider: **GroqCloud** for model calls; deterministic V6 is the fallback (no model tokens).
- Requests covered: **{selected}**
- Cases with accepted model tool submission: **{agent_rows}**
- Cases using V6 baseline/fallback: **{baseline_rows}**
- Cases attempted with model: **{usage.cases_with_model}**
- Model completions: **{usage.calls}**
- Prompt/input tokens: **{usage.input_tokens}**
- Completion/output tokens: **{usage.output_tokens}**
- Total tokens: **{usage.input_tokens + usage.output_tokens}**
- Avg input tokens per selected request: **{avg_input:.2f}**
- Avg output tokens per selected request: **{avg_output:.2f}**
- Avg total tokens per selected request: **{avg_input + avg_output:.2f}**
- Avg model calls per selected request: **{usage.calls / selected if selected else 0:.3f}**
- Rejected tool submissions: **{usage.rejected_submissions}**
- Fallback count: **{usage.fallback_cases}** (reasons: {fallback_reasons})
- Local tool calls: {tool_usage}

## Estimated Cost

- List-price input: **${input_price:.3f} per million tokens**
- List-price output: **${output_price:.3f} per million tokens**
- Estimated total usage at list pricing: **${estimated:.6f}**
- Estimated usage per selected request: **${estimated / selected if selected else 0:.8f}**
- Price source: https://console.groq.com/docs/model/openai/gpt-oss-20b (verify current pricing before submission).
- Actual Groq billing/free-tier charges may differ. Unknown model pricing is reported with zero configured price, not a guarantee of free inference.
- Model calls and token counts come from provider `usage`; retry attempts that never returned a usable response may be undercounted.

## Runtime Audit

A model decision counts only if it performed local function-tool calls and its
`submit_audit` was accepted by strict policy/evidence validation. The V6 fallback
is labeled distinctly and does **not** count as agent inference. All plans pass
schema validation; output IDs/ordering and checkpoint fingerprints are checked.
No prompts, account identifiers, API responses, or credentials are stored here.
This is a usage report, not evidence of hidden-label accuracy.
'''


def main(argv: list[str] | None = None, *, client_override: Any = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0 or args.max_rounds < 2 or args.delay < 0 or args.max_agent_calls < 0:
        raise ValueError('Invalid negative limit/delay/calls or max-rounds < 2')
    root = Path(__file__).resolve().parents[1]
    dataset = load_dataset(root, sample=args.sample)
    requests = sorted(dataset.requests, key=lambda r: r.case_id)
    if args.limit:
        requests = requests[:args.limit]
    is_partial = args.sample or bool(args.limit)
    if is_partial and not args.out:
        args.out = root / 'code' / 'evaluation' / 'step9_sample_predictions.csv'
    if is_partial and args.out.resolve() == (root / 'output.csv').resolve():
        raise ValueError('Sample/partial runs may not overwrite root output.csv')
    output = args.out or (root / 'output.csv')
    usage_path = args.usage_report or (root / 'code' / 'evaluation' / 'usage_report.md')
    checkpoint_path = args.checkpoint or (root / ('step9_sample.checkpoint.jsonl' if is_partial else 'step9_full.checkpoint.jsonl'))

    if args.mode == 'auto':
        mode = 'agent' if os.environ.get('GROQ_API_KEY') or client_override is not None else 'baseline'
    else:
        mode = args.mode
    if mode == 'agent':
        client = client_override or GroqHTTPClient(model=args.model, delay=args.delay)
    else:
        client = None
        if args.strict_agent:
            raise ValueError('--strict-agent requires real agent mode')
    print(f'STEP9 MODE: {mode} | MODEL: {args.model} | CASES: {len(requests)}', flush=True)
    if mode == 'baseline':
        print('NOTICE: deterministic V6 fallback, NO Groq model calls; not equivalent to agentic inference', flush=True)

    fingerprint = _input_fingerprint(dataset, mode, args.model, args.sample, args.limit)
    if args.resume:
        previous = _existing_checkpoint(checkpoint_path, fingerprint)
    else:
        previous = []
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps({'type': 'header', 'fingerprint': fingerprint}) + '\n', encoding='utf-8')
    index = build_index(dataset.evidence, enhanced_readback=True, decode_sql_json=True,
                        structured_attachment=True, structured_event_v6=True)
    resolver = IdentityResolver(dataset.identities, dataset.changes)
    ids = frozenset(e.file_id for e in dataset.evidence)
    usage = Usage(model=args.model)
    results: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    counts = Counter()
    request_ids = {r.case_id for r in requests}
    for record in previous:
        case_id = record.get('case_id')
        if case_id not in request_ids or case_id in results:
            raise ValueError('Invalid or duplicate checkpoint case ID')
        validate_prediction(record['status'], record['steps'], evidence_ids=ids)
        results[case_id] = (record['status'], record['steps'])
        counts[record['source']] += 1
        # Restore prior usage without counting resumed tool calls twice.
        delta = record.get('usage_delta', {})
        for field in ('calls', 'input_tokens','output_tokens','cases_with_model','fallback_cases',
                      'rejected_submissions','accepted_submissions'):
            setattr(usage, field, getattr(usage, field) + int(delta.get(field, 0)))
        for field in ('tool_calls', 'reasons'):
            dest = getattr(usage, field)
            for key, number in delta.get(field, {}).items():
                dest[key] = dest.get(key, 0) + int(number)
    if previous:
        print(f'RESUMED: {len(previous)} committed cases', flush=True)
    for pos, request in enumerate(requests, 1):
        if request.case_id in results:
            continue
        identity = resolver.resolve(request.email, request.audit_at)
        draft = plan_case(request, identity, index, dataset.rules, variant='structured-event-v6')
        validate_prediction(draft.status, draft.steps, evidence_ids=ids)
        before = usage.as_dict()
        if mode == 'agent' and (not args.max_agent_calls or usage.calls < args.max_agent_calls):
            tools = CaseTools(request, identity, index, dataset.rules, draft, ids)
            result = run_case_agent(tools, client, usage, max_rounds=args.max_rounds)
        else:
            reason = 'no_key_baseline_mode' if mode == 'baseline' else 'agent_call_budget_exhausted'
            usage.fallback(reason)
            result = AgentResult(draft, 'v6_safety_fallback', reason)
        if args.strict_agent and result.source != 'groq_tool_agent':
            raise ProviderError(f'Agent incomplete at case {request.case_id}; reason={result.reason}; strict mode stopped without fabricated inference')
        validate_prediction(result.plan.status, result.plan.steps, evidence_ids=ids)
        results[request.case_id] = (result.plan.status, result.plan.steps)
        counts[result.source] += 1
        after = usage.as_dict()
        delta = {key: after[key] - before[key] for key in ('calls', 'input_tokens','output_tokens',
                    'cases_with_model', 'fallback_cases','rejected_submissions','accepted_submissions')}
        delta['tool_calls'] = {k: n - before['tool_calls'].get(k, 0) for k, n in after['tool_calls'].items()
                                if n - before['tool_calls'].get(k, 0)}
        delta['reasons'] = {k: n - before['reasons'].get(k, 0) for k, n in after['reasons'].items()
                            if n - before['reasons'].get(k, 0)}
        _append_checkpoint(checkpoint_path, {'case_id': request.case_id, 'status': result.plan.status,
                                             'steps': result.plan.steps, 'source': result.source,
                                             'usage_delta': delta})
        if pos % 25 == 0 or pos == len(requests):
            print(f'PROGRESS {len(results)}/{len(requests)} | model-accepted {counts["groq_tool_agent"]} | '
                  f'fallback {counts["v6_safety_fallback"]} | API calls {usage.calls}', flush=True)
    _write_csv(output, results, expected_case_ids=[r.case_id for r in requests])
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(usage_markdown(usage, selected=len(requests),
                baseline_rows=counts['v6_safety_fallback'], agent_rows=counts['groq_tool_agent'],
                mode=mode, path=output, sample=is_partial, completed=True), encoding='utf-8')
    print(f'OUTPUT: {output} | ROWS: {len(results)} | STATUS: {dict(Counter(x[0] for x in results.values()))}', flush=True)
    print(f'USAGE REPORT: {usage_path} | REAL MODEL CALLS: {usage.calls}', flush=True)
    print(f'EVIDENCE: {len(index.examined_files)}/{len(dataset.evidence)} files, '
          f'UNPARSED MEDIA: {len(index.unparsed_media)}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ProviderError, RuntimeError) as exc:
        print('STEP9 ERROR: ' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
