# Diagnostic evaluation (Step 7)

Run all commands from the repository root. Python 3.10+ is required; no external packages or API keys are used.

## Custom synthetic tests

```powershell
python .\code\evaluation\main.py --custom-only
python -m unittest discover -s .\code\tests -p "test_*.py" -v
```

## Smoke evaluation, one real prediction

```powershell
python .\code\evaluation\prepare_smoke.py --case-id case_05 --evidence-id evidence_54
python .\code\evaluation\main.py --predictions .\code\evaluation\smoke_predictions.csv --allow-partial
```

This runs the existing Step 6 single-evidence smoke path for `case_05`. It deliberately generates only one prediction. The evaluator reports all missing labels as uncovered; this is not a 32-case model baseline.

## Full 32-case evaluation

After the inference pipeline can process **all** public sample requests without reading sample answers, save its 32 predictions as a CSV:

```powershell
python .\code\evaluation\main.py --predictions .\path\to\sample_predictions_A.csv
```

The evaluator reads `dataset/sample_output.csv` only in this directory, not during inference. The prediction file must contain `case_id,deletion_status,steps_json` exactly. Extra or missing case IDs fail a full run.

## A/B comparison

```powershell
python .\code\evaluation\main.py --predictions .\path\to\sample_predictions_A.csv --compare .\path\to\sample_predictions_B.csv
```

The saved `evaluation_report.md` includes absolute metrics, class confusion pairs, operation-level errors, and A/B deltas. The adjacent `mismatches.jsonl` contains every difference for diagnosis and should not be used for inference. Metrics are reference-relative and are not estimates of hidden-set performance.

## Metric definitions

- **Status accuracy:** number with correct status / 32, counting missing predictions as wrong.
- **Step precision / recall:** multiset-exact matches (phase, system, resource, operation, state, evidence ID); order independently scored.
- **Field accuracy:** 7 fields compared position by position; extra and missing steps are penalized.
- **Sequence accuracy:** entire ordered steps array matches exactly, including consecutive step numbers.
- **Exact row accuracy:** both status and sequence are correct.
- **Contract validity:** checks allowed values, phase order, evidence citation IDs, citation timestamps against each case's `audit_at`, and future-action empty citations.
- **Synthetic cases:** test evaluator behavior on no-deletion authorization, an evidenced remaining record, and a required retained record. These are not end-to-end hidden cases.
