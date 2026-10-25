# Step 8: First 32-case baseline

All commands run from the repository root in PowerShell. The prediction runner never reads `sample_output.csv`.

```powershell
python -m pip install --user pymupdf pillow pytesseract
python -m unittest discover -s .\code\tests -p "test_*.py" -v
python .\code\run_baseline.py --sample --variant latest --out .\code\evaluation\baseline_latest.csv
python .\code\evaluation\main.py --predictions .\code\evaluation\baseline_latest.csv
```

Optional Tesseract OCR executable must be installed separately; `pytesseract` alone is not enough. Any skipped images appear in `baseline_latest.diagnostics.json` and must be acknowledged. Do not assume OCR coverage or claim a complete multimodal audit when any media remains unreadable.

For a controlled one-variable A/B test:

```powershell
python .\code\run_baseline.py --sample --variant replica-safe --out .\code\evaluation\baseline_replica_safe.csv
python .\code\evaluation\main.py --predictions .\code\evaluation\baseline_replica_safe.csv --compare .\code\evaluation\baseline_latest.csv
```

Do not run `run_baseline.py` on the 518 hidden cases in this stage. This is a diagnostic baseline, not final production inference or an agentic orchestration loop. Preserve the full mismatch list for iterative debugging. Model calls: 0. Model tokens: 0. Inference cost: $0.
