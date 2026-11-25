"""Run:  python -m unittest discover -s tests -v   (pure logic for the mapping and script modules)"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mapping
import scan

SAMPLE = ROOT / "samples" / "Source_Target_Mapping_Example.xlsx"


class MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = mapping.load_xlsx(SAMPLE.read_bytes())

    def test_reads_both_sheets_and_reference_sql(self):
        self.assertEqual(len(self.model["mappings"]), 17)
        self.assertEqual(len(self.model["rules"]), 15)          # the reference SQL rows are not rules
        self.assertIn("INSERT INTO DWH.FACT_ACCOUNT_DAILY", self.model["reference_sql"])
        self.assertEqual(self.model["mappings"][5]["target_column"], "CLOSING_BALANCE_CAD")

    def test_summary(self):
        s = mapping.summary(self.model)
        self.assertEqual(s["joins"], {"INNER": 1, "LEFT": 5})
        self.assertEqual(s["parameters"], ["run_date"])
        self.assertEqual(s["target_tables"], ["DWH.FACT_ACCOUNT_DAILY"])

    def test_clean_document_has_no_problems(self):
        self.assertEqual([i for i in mapping.lint(self.model) if i["severity"] in ("high", "medium")], [])

    def test_lint_finds_planted_problems(self):
        m = mapping.load_xlsx(SAMPLE.read_bytes())
        m["mappings"].append(dict(m["mappings"][0]))            # duplicate Map ID and target column
        m["mappings"][1]["logic"] = ""
        m["mappings"][1]["source_column"] = ""
        m["rules"][1]["expr"] = "zz.ACCT_ID = a.ACCT_ID"        # alias zz is never defined
        m["rules"][2]["expr"] = ""                              # join with no condition
        m["rules"][3]["applies"] = "M99"
        text = " | ".join(i["where"] + ": " + i["message"] for i in mapping.lint(m))
        for expected in ("used more than once", "nothing can fill it", "alias 'zz'", "no condition", "M99"):
            self.assertIn(expected, text)

    def test_reference_sql_misses_its_own_rules(self):
        cov = mapping.coverage(self.model, self.model["reference_sql"])
        by_rule = {r["ref"]: r["status"] for r in cov["rows"] if r["ref"].startswith("Rule")}
        for rule in ("Rule 10", "Rule 11", "Rule 13", "Rule 14"):
            self.assertEqual(by_rule[rule], "missing", rule)
        self.assertEqual(by_rule["Rule 9"], "verify")             # only half of the test-account rule is there
        self.assertEqual(by_rule["Rule 2"], "found")
        self.assertTrue(cov["positional_insert"])

    def test_complete_code_is_found(self):
        code = """INSERT INTO T (A) SELECT x AS A FROM s db JOIN b ON db.k = b.k WHERE db.d >= :run_date - 3 GROUP BY k"""
        model = {"mappings": [{"id": "M1", "target_column": "A", "logic": "", "source_column": "x", "source_schema": "", "source_table": "S", "target_table": "T", "key": ""}],
                 "rules": [{"seq": "1", "type": "Join", "name": "j", "left": "S (db)", "right": "B (b)", "join_type": "INNER", "expr": "db.k = b.k", "logic": "", "applies": "M1"},
                           {"seq": "2", "type": "Filter", "name": "f", "left": "db", "right": "-", "join_type": "-", "expr": "db.d >= :run_date - 3", "logic": "", "applies": "M1"},
                           {"seq": "3", "type": "Aggregation", "name": "g", "left": "-", "right": "-", "join_type": "-", "expr": "GROUP BY k", "logic": "", "applies": "All"}]}
        cov = mapping.coverage(model, code)
        self.assertEqual(cov["counts"]["missing"], 0, cov["rows"])
        self.assertEqual(cov["counts"]["found"], 5)               # column, join, filter, aggregation, parameter

    def test_copilot_style_wording_does_not_cause_false_alarms(self):
        """Replies often say 'INNER (plain JOIN)', list several tables in one cell, or add 'meaning ...' prose."""
        model = {"mappings": [{"id": "M1", "target_column": "A", "logic": "Direct move", "source_column": "x", "source_schema": "", "key": "PK", "target_table": "T",
                               "source_table": "order_line (l), product (p), customer (c)"},
                              {"id": "M2", "target_column": "B", "logic": "Direct move", "source_column": "y", "source_schema": "", "key": "", "target_table": "T",
                               "source_table": "order_tot (o), derived from M1"}],
                 "rules": [{"seq": "1", "type": "Driver", "name": "base", "left": "order_line (l)", "right": "-", "join_type": "-", "expr": "FROM order_line l", "logic": "", "applies": "M1"},
                           {"seq": "2", "type": "Join", "name": "cust", "left": "order_line (l)", "right": "customer (c)", "join_type": "INNER (plain JOIN)",
                            "expr": "USING (customer_id), meaning l.customer_id = c.customer_id", "logic": "", "applies": "M1"},
                           {"seq": "3", "type": "Join", "name": "prod", "left": "order_line (l)", "right": "product (p)", "join_type": "INNER (plain JOIN)", "expr": "USING (sku)", "logic": "", "applies": "M1"},
                           {"seq": "4", "type": "Join", "name": "tot", "left": "order_tot (o)", "right": "x (x)", "join_type": "LEFT", "expr": "o.k = x.k", "logic": "", "applies": "M2"}]}
        for row in model["mappings"]:                       # parsed models always carry every field
            for field, _ in mapping.MAP_HEADERS:
                row.setdefault(field, "")
        for row in model["rules"]:
            for field, _ in mapping.RULE_HEADERS:
                row.setdefault(field, "")
        self.assertEqual([i for i in mapping.lint(model) if i["severity"] in ("high", "medium")], [])
        self.assertEqual(mapping.summary(model)["joins"], {"INNER": 2, "LEFT": 1})
        self.assertEqual(sorted(mapping.summary(model)["source_tables"]), ["customer", "order_line", "order_tot", "product"])
        code = "SELECT * FROM order_line l JOIN customer c USING (customer_id) JOIN product p USING (sku)"
        cov = mapping.coverage(model, code)
        by_rule = {r["ref"]: r["status"] for r in cov["rows"] if r["ref"].startswith("Rule")}
        self.assertEqual((by_rule["Rule 2"], by_rule["Rule 3"]), ("found", "found"))

    def test_unknown_schema_is_not_shown_as_part_of_the_table_name(self):
        model = {"mappings": [{"source_schema": "UNKNOWN", "source_table": "order_line (l)", "source_column": "x"}], "rules": []}
        self.assertEqual(list(mapping.source_tables(model)), ["order_line"])

    def test_code_diff_handles_sql_comment_lines(self):
        import mapdiff
        old = "-- old comment\nSELECT a FROM t;\n++ odd\n"
        new = "-- new comment\nSELECT a FROM t;\n"
        d = mapdiff.diff_code(old, new)
        self.assertEqual((d["added"], d["removed"]), (1, 2))             # the two '--' / '++' lines count like any other line
        kinds = [(l["kind"], l["text"]) for l in d["lines"]]
        self.assertIn(("del", "--- old comment"), kinds)
        self.assertIn(("add", "+-- new comment"), kinds)
        self.assertTrue(mapdiff.diff_code("x\n", "x\n")["same"])

    def test_write_then_read_round_trip(self):
        data = mapping.write_xlsx(self.model)
        again = mapping.load_xlsx(data)
        self.assertEqual(again["mappings"], self.model["mappings"])
        self.assertEqual(again["rules"], self.model["rules"])

    def test_not_an_xlsx(self):
        with self.assertRaises(ValueError):
            mapping.read_xlsx(b"this is not a workbook")

    def test_reply_tables_become_a_model(self):
        reply = """Here you go
```
## Source_Target_Mapping
| Map ID | Source Schema | Source Table | Source Column | Target Schema | Target Table | Target Column | Transformation / Business Logic |
|---|---|---|---|---|---|---|---|
| M01 | CBS | ACCOUNT | ACCT_ID | DWH | FACT | ACCOUNT_ID | Direct move |
## Joins_Filters
| Seq | Type | Rule Name | Left Table (Alias) | Right Table (Alias) | Join Type | Join Condition / Filter Expression | Applies To Map IDs |
|---|---|---|---|---|---|---|---|
| 1 | Driver | Base | CBS.ACCOUNT (a) | - | - | FROM CBS.ACCOUNT a | M01 |
```
"""
        m = mapping.model_from_reply(reply)
        self.assertEqual(m["mappings"][0]["target_column"], "ACCOUNT_ID")
        self.assertEqual(m["rules"][0]["type"], "Driver")
        self.assertEqual(m["mappings"][0]["nullable"], "")        # missing columns become blanks
        with self.assertRaises(ValueError):
            mapping.model_from_reply("no tables here")


class ScanTests(unittest.TestCase):
    SQL = "-- load\nSELECT * FROM a, b;\nINSERT INTO t SELECT x FROM c LEFT JOIN d ON c.k = d.k WHERE c.v NOT IN (SELECT v FROM e);\nUPDATE t SET x = 1;\n"
    PY = 'import csv\npassword = "hunter2"\nRATE = 1.35\nwith open("/data/in.csv") as f:\n    for r in csv.reader(f):\n        x = round(float(r[0]) * RATE, 2)\n'

    def test_sql_facts_and_findings(self):
        f = scan.scan_file("x.sql", self.SQL)
        self.assertEqual(f["language"], "SQL")
        self.assertIn("C", f["tables_read"])
        self.assertEqual(f["join_count"], 1)
        titles = {x["title"] for x in f["findings"]}
        for expected in ("SELECT *", "Comma join in FROM", "NOT IN with a subquery", "UPDATE or DELETE without WHERE", "INSERT without a column list"):
            self.assertIn(expected, titles)

    def test_python_findings_and_hardcoded_values(self):
        f = scan.scan_file("job.py", self.PY)
        titles = {x["title"] for x in f["findings"]}
        self.assertIn("Password or secret in the script", titles)
        self.assertIn("Float used for amounts", titles)
        self.assertIn("Hardcoded file paths", titles)
        self.assertIn("1.35", [h["value"] for h in f["hardcoded"]])
        self.assertEqual(f["findings"][0]["severity"], "high")    # worst first

    def test_language_is_recognised_from_content_when_the_name_has_no_extension(self):
        sas = "proc sql;\n create table t as select a from b;\nquit;\n"
        cobol = "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. X.\n"
        for name, text, lang in (("job", self.PY, "Python"), ("job", self.SQL, "SQL"), ("job", sas, "SAS"), ("job", cobol, "COBOL"),
                                 ("job", "#!/bin/bash\ncp a b\n", "Shell"), ("job", "just words", "Unknown"), ("x.py", self.SQL, "Python")):
            self.assertEqual(scan.language_of(name, text), lang, (name, lang))

    def test_summary_is_plain_english(self):
        r = scan.scan_sources({"x.sql": self.SQL, "job.py": self.PY})
        self.assertIn("is a SQL script", r["summary"])
        self.assertIn("Overall complexity", r["summary"])
        self.assertIn("Tables read", scan.facts_text(r))


if __name__ == "__main__":
    unittest.main()
