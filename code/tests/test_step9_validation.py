"""Output artifact acceptance checks."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate_output import validate_file


class ValidationTests(unittest.TestCase):
    def test_reject_wrong_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'output.csv'
            p.write_text('case_id,status,steps_json\na,COMPLETE,[]\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                validate_file(p, Path(__file__).resolve().parents[2], sample=True)

    def test_reject_missing_case_even_if_rows_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'output.csv'
            with p.open('w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f); writer.writerow(('case_id','deletion_status','steps_json'))
                writer.writerow(('case_01','COMPLETE','[]'))
            with self.assertRaises(ValueError):
                validate_file(p, Path(__file__).resolve().parents[2], sample=True)

if __name__ == '__main__':
    unittest.main()
