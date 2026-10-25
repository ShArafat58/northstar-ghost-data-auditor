# Step 8: Embedded SQL JSON V4 — isolated A/B experiment

This patch addresses one identified retrieval issue: some SQLite-compatible SQL snapshots contain a typed `record_json` payload inside each row of `customer_data_primary` or `customer_data_read_replica`. The prior reader saw only wrapper fields and therefore could not link those records to an audited identity.

## What changes

- `code/observations.py`: optional `decode_sql_json` on `build_index`, and `decode_record_json` on `scan_sql`. Requires envelope/payload resource IDs to match, requires the resource's expected system to match the envelope's `origin_system`, and reads only known identity columns from the record and nested `document`/`profile`. Supports a direct token in `subject_selector`. Does not associate neighboring rows or parse arbitrary descriptions as identity matches.
- `code/baseline.py`: accepts `embedded-sql-v4` using the established Intent V2 / Readback V2 planner. No changes to the planner's decision or sorting logic.
- `code/run_baseline.py`: CLI exposes the new `embedded-sql-v4` variant. All other existing variants remain unchanged.
- `code/tests/test_embedded_sql_v4.py`: eight synthetic regression tests, including corruption handling, cross-user and future-evidence exclusion.

The change does **not** repair PDF or PNG structured parsing, missing proof of absence, or the issue of classifying an empty proof set as COMPLETE. Those require separate, evidence-grounded experiments.

## Commands (repo root, PowerShell)

```powershell
python -m unittest discover -s .\code\tests -p "test_*.py" -v
$env:PATH = "C:\Program Files\Tesseract-OCR;$env:PATH"
python .\code\run_baseline.py --sample --variant readback-v2 --out .\code\evaluation\baseline_readback_v2_v4_control.csv
python .\code\run_baseline.py --sample --variant embedded-sql-v4 --out .\code\evaluation\baseline_embedded_sql_v4.csv
python .\code\evaluation\main.py --predictions .\code\evaluation\baseline_readback_v2_v4_control.csv --compare .\code\evaluation\baseline_embedded_sql_v4.csv
Select-String -Path .\code\evaluation\evaluation_report.md -Pattern "## Side-by-side A/B comparison" -Context 0,18
python .\code\evaluation\main.py --predictions .\code\evaluation\baseline_embedded_sql_v4.csv
```

Record real metrics only, and retain the existing `readback-v2` as control. No production 518-case run and no use of labelled sample answers in the prediction pipeline.
