"""Unit tests and documentation for generated code.  Run:  python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mapping
import qa

SAMPLE = ROOT / "samples" / "Source_Target_Mapping_Example.xlsx"


def reply_with(tests, code="INSERT INTO T (A) SELECT 1 AS A -- M01"):
    return "```sql\n%s\n```\n\n## Unit tests\n\n```python\n%s\n```\n\n## Assumptions and open questions\n- Rate is daily.\n- Branch may be null.\n\n## Rule traceability\n| a | b |\n|---|---|\n| M01 | line 1 |\n" % (code, tests)


class Extraction(unittest.TestCase):
    def test_code_and_tests_are_separated(self):
        reply = reply_with("def test_M01_x():\n    assert 1 == 1")
        code, tests = qa.split_reply(reply)
        self.assertIn("INSERT INTO", code)
        self.assertNotIn("def test_", code)
        self.assertTrue(qa.extract_tests(reply).startswith("def test_M01_x"))

    def test_reply_without_tests(self):
        self.assertEqual(qa.extract_tests("```sql\nSELECT 1\n```\n## Assumptions and open questions\n- x"), "")

    def test_sections(self):
        reply = reply_with("def test_a():\n    assert 1")
        self.assertEqual(qa.reply_section(reply, "Assumptions and open questions"), "- Rate is daily.\n- Branch may be null.")
        self.assertEqual(qa.reply_section(reply, "Nothing here"), "")


class Checking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = mapping.load_xlsx(SAMPLE.read_bytes())

    def test_every_column_and_rule_needs_a_real_test(self):
        ids = [m["id"] for m in self.model["mappings"]]
        body = []
        for i in ids[3:]:                                   # M01..M03 get no test at all
            body.append('def test_%s_col():\n    """%s"""\n    assert run() == 1\n' % (i, i))
        body.append('def test_M01_weak():\n    """M01"""\n    assert True\n')            # asserts nothing real
        body.append('def test_M02_skipped():\n    """M02"""\n    import pytest\n    pytest.skip("engine lacks this")\n    assert False\n')
        for x in self.model["rules"]:
            body.append('def test_rule_%s_x():\n    """Rule %s"""\n    assert run() == 1\n' % (x["seq"], x["seq"]))
        code = "import duckdb\n\ndef run():\n    return duckdb.connect().execute('select 1').fetchone()[0]\n\n" + "\n".join(body)
        r = qa.check_tests(self.model, code, "sql")
        status = {x["ref"]: x["status"] for x in r["rows"]}
        self.assertTrue(r["parses"])
        self.assertEqual((status["M01"], status["M02"], status["M03"], status["M04"]), ("weak", "skipped", "missing", "tested"))
        self.assertEqual(status["Rule 1"], "tested")
        self.assertEqual(r["counts"]["tested"] + r["counts"]["weak"] + r["counts"]["skipped"] + r["counts"]["missing"], r["total"])
        self.assertEqual(r["problems"], [])

    def test_rule_number_matching_is_exact(self):
        model = {"mappings": [], "rules": [{"seq": "1", "type": "Join", "name": "a", "expr": "x"}, {"seq": "11", "type": "Join", "name": "b", "expr": "y"}]}
        code = 'def test_rule_11_b():\n    assert len("a") == 1\n'
        status = {x["ref"]: x["status"] for x in qa.check_tests(model, code, "")["rows"]}
        self.assertEqual((status["Rule 1"], status["Rule 11"]), ("missing", "tested"))

    def test_map_id_matching_is_exact(self):
        model = {"mappings": [{"id": "M1", "target_column": "A"}, {"id": "M10", "target_column": "B"}], "rules": []}
        status = {x["ref"]: x["status"] for x in qa.check_tests(model, "def test_M10_b():\n    assert len('a') == 1\n", "")["rows"]}
        self.assertEqual((status["M1"], status["M10"]), ("missing", "tested"))

    def test_broken_python_is_reported(self):
        r = qa.check_tests(self.model, "def test_(:\n", "sql")
        self.assertFalse(r["parses"])
        self.assertIn("not valid Python", r["error"])
        self.assertFalse(qa.check_tests(self.model, "", "sql")["parses"])

    def test_tests_that_never_run_the_code_are_flagged(self):
        r = qa.check_tests(self.model, "def test_M01():\n    assert len('a') == 1\n", "pyspark")
        self.assertTrue(any("build_target" in p for p in r["problems"]))


class Documentation(unittest.TestCase):
    def test_document_has_every_section_and_the_facts(self):
        model = mapping.load_xlsx(SAMPLE.read_bytes())
        tests = "def test_M01_account_id():\n    assert len('a') == 1\n"
        ctx = {"name": "demo", "version": 2, "target": "sql", "model": model, "summary": mapping.summary(model), "lint": mapping.lint(model),
               "coverage": mapping.coverage(model, model["reference_sql"]), "tests": qa.check_tests(model, tests, "sql"), "assumptions": "- Rate is daily.",
               "approved": True, "files": ["generated.sql", "documentation.md"], "changes": "1 rule changed.", "when": "Generated today"}
        doc = qa.build_documentation(ctx)
        for text in ("# demo", "Version 2 - SQL", "## 3. Column mapping", "ACCOUNT_ID", "## 4. Joins", "`:run_date`", "signed off", "## 8. Unit tests",
                     "without a test", "1 rule changed.", "Rate is daily.", "*AI draft*", "How the mapping file was read"):
            self.assertIn(text, doc)
        self.assertNotIn("|  |", doc)

    def test_pipes_in_cells_do_not_break_tables(self):
        self.assertIn("a\\|b", qa._table(["h"], [["a|b"]]))


if __name__ == "__main__":
    unittest.main()
