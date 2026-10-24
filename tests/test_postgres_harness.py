import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.postgres_harness import _assert_disposable, _split_sql


class PostgreSQLHarnessSafetyTests(unittest.TestCase):
    def test_sql_splitter_keeps_simple_statements_ordered(self):
        self.assertEqual(_split_sql("select 1;\n select 2;"), ["select 1", "select 2"])

    def test_disposable_target_requires_exact_database_identity(self):
        with patch.dict(os.environ, {"SMARTPARK_DISPOSABLE_DB_NAME": "smartpark_test"}, clear=False):
            _assert_disposable(("smartpark_test", "tester", "127.0.0.1"))
            with self.assertRaises(SystemExit):
                _assert_disposable(("smartpark_other", "tester", "127.0.0.1"))

    def test_shared_or_production_target_is_rejected(self):
        with patch.dict(os.environ, {"SMARTPARK_DISPOSABLE_DB_NAME": "smartpark_test"}, clear=False):
            with self.assertRaises(SystemExit):
                _assert_disposable(("smartpark_test", "tester", "shared-db.internal"))

    def test_non_disposable_name_is_rejected(self):
        with patch.dict(os.environ, {"SMARTPARK_DISPOSABLE_DB_NAME": "smartpark_main"}, clear=False):
            with self.assertRaises(SystemExit):
                _assert_disposable(("smartpark_main", "tester", "127.0.0.1"))
