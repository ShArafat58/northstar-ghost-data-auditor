# Step 9 — Groq Tool-Using Deletion Auditor

## Architecture

`code/main.py` runs all 518 deletion requests through a single reusable, cutoff-aware `EvidenceIndex` built by the **unchanged** Structured Event V6 engine. The agent is hosted on Groq with the `openai/gpt-oss-20b` model. A real local function/tool-call protocol gives the model access to:

1. `get_case_dossier` — authorized request, historical identity, grounded resource summary, conservative draft.
2. `get_resource_history` — timestamped evidence for an identity-linked resource.
3. `lookup_policy` — supplied rules for the selected data category.
4. `submit_audit` — model-produced final status and ordered plan, validated on the server side.

The model chooses its tool interactions and final proposal. **It cannot replace verified findings or remediation with guesses.** Additional verification steps are accepted only when backed by subject-linked evidence, matching state, source capture time, rule category, and legal output operations. Hallucinations, bad tool responses, model outages and rate limits fall back to the unchanged V6 plan and are counted separately. The model **never executes deletion**; operations are proposed audit steps.

The agent's system prompt is in `agent_step9.py` under `SYSTEM_PROMPT`, and tool definitions are in `DOSSIER_TOOL`/`TOOLS`. No sample answers are imported, and user request/evidence text is treated as untrusted data.

## Requirements

- Python 3.12+; no third-party Groq SDK needed (`urllib.request`).
- Existing repository Step 8 V6 dependencies: PyMuPDF, Pillow, `pytesseract`, Tesseract OCR installed and available on PATH.
- Local dataset folder next to code, containing 518 deletion requests and 94 evidence files.
- `GROQ_API_KEY` environment variable for actual Groq calls, never committed or copied to a chat/log.

## Windows PowerShell

Run from the repository root:

```powershell
$env:PATH = "C:\Program Files\Tesseract-OCR;$env:PATH"
python -m unittest discover -s .\code\tests -p "test_*.py" -v

# No key: offline safety baseline (not agent inference), partial smoke only
python .\code\main.py --mode baseline --sample --limit 5 --out .\code\evaluation\step9_offline_smoke.csv --usage-report .\code\evaluation\step9_offline_smoke_usage.md --checkpoint .\code\evaluation\step9_offline_smoke_checkpoint.jsonl

# To test real Groq without exposing a key in the console:
$secret = Read-Host 'Groq key' -AsSecureString
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password

# Explicit real-agent smoke run: never writes the root submission output
python .\code\main.py --mode agent --sample --limit 5 --out .\code\evaluation\step9_agent_smoke.csv --usage-report .\code\evaluation\step9_agent_smoke_usage.md --checkpoint .\code\evaluation\step9_agent_smoke_checkpoint.jsonl

# The 5-case smoke is a PROTOCOL check, not a 32-case accuracy measurement.
# For genuine labelled evaluation, run all 32 sample cases with a distinct
# --out, --checkpoint and --usage-report, then evaluate that output.

# Only after reviewing Groq calls and agent-accepted vs fallback counts:
python .\code\main.py --mode agent --out .\output.csv --usage-report .\code\evaluation\usage_report.md --checkpoint .\step9_full.checkpoint.jsonl

# If interrupted, resume with the same configuration:
python .\code\main.py --mode agent --out .\output.csv --usage-report .\code\evaluation\usage_report.md --checkpoint .\step9_full.checkpoint.jsonl --resume

# Optionally disallow ALL fallback for strict, verified live runs:
# python .\code\main.py --mode agent --strict-agent --out .\output.csv

# Remove the key from this terminal after inference:
Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue
```

**Important:** Do not run a full live 518-case job until you have checked remaining Groq rate and token limits. Your model calls consume quota. `--delay` (default 1.0 second/request), `--max-agent-calls` (default 1600) and `--max-rounds` (default 5) can be tuned. A model run that runs out of calls uses and explicitly reports V6 fallbacks. A sample run uses a different checkpoint; keep it separate from production. `--resume` refuses changed input, mode or model.

## Outputs and limitations

- Final `output.csv` is root-level and contains exactly 518 rows, sorted by `case_id`. Check it independently with `python code/validate_output.py --out output.csv`.
- `code/evaluation/usage_report.md` is overwritten with REAL model usage from your execution. A prior placeholder is **not** a final usage report.
- `code/evaluation/evaluation_report.md` is the existing V6 sample benchmark; running sample evaluation overwrites it. Do not conflate sample accuracy and hidden ranking.
- `step9_full.checkpoint.jsonl` (repo root) is a crash-recovery checkpoint. Do not include JSONL checkpoints in `code.zip`.
- Current V6 benchmark: 32/32 sample statuses, 160/193 exact steps, 10/32 exact full rows. This does not guarantee hidden-set performance or a Top-10 rank.
- 14 media files lack structured observations; an LLM is not authorized to infer deleted/absent records from unparsed media.
- Groq is a remote processor; the model API receives request/evidence context from the participant-provided dataset. Do not use on real customer PII without the appropriate permissions.

## Submission packaging

Run from repo root after `code/evaluation/usage_report.md` reflects the FINAL live run:

```powershell
# Remove transient runtime/test-generated files from code folder before packaging
Get-ChildItem .\code -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Compress-Archive -Path README.md, code -DestinationPath code.zip -Force
```

Do not put `dataset/`, keys, cached model messages, checkpoint JSONL or nested ZIP archives in `code.zip`. Upload `log.txt` separately as the chat transcript. Verify final 518-row `output.csv` independently.
