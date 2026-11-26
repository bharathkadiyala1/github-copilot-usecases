"""Run:  python -m unittest discover -s tests -v"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app
import core


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def cmp(self, expected, actual, **kw):
        write(self.tmp / "e.csv", expected)
        write(self.tmp / "a.csv", actual)
        return core.compare_csv(self.tmp / "e.csv", self.tmp / "a.csv", **kw)

    def test_identical_passes(self):
        self.assertTrue(self.cmp("id,v\n1,2.00\n", "id,v\n1,2.00\n")["passed"])

    def test_value_difference_reports_cell(self):
        r = self.cmp("id,v\n1,2.00\n2,3.00\n", "id,v\n1,2.00\n2,3.01\n", key_columns=["id"])
        self.assertFalse(r["passed"])
        self.assertEqual(r["diffs"][0]["column"], "v")
        self.assertEqual((r["diffs"][0]["expected"], r["diffs"][0]["actual"]), ("3.00", "3.01"))

    def test_tolerance(self):
        self.assertTrue(self.cmp("id,v\n1,2.00\n", "id,v\n1,2.01\n", tolerance="0.01")["passed"])
        self.assertFalse(self.cmp("id,v\n1,2.00\n", "id,v\n1,2.02\n", tolerance="0.01")["passed"])

    def test_key_mode_ignores_order(self):
        self.assertTrue(self.cmp("id,v\n1,a\n2,b\n", "id,v\n2,b\n1,a\n", key_columns=["id"])["passed"])
        self.assertFalse(self.cmp("id,v\n1,a\n2,b\n", "id,v\n2,b\n1,a\n")["passed"])

    def test_missing_and_extra_rows(self):
        r = self.cmp("id,v\n1,a\n2,b\n", "id,v\n1,a\n3,c\n", key_columns=["id"])
        self.assertEqual(sorted(d["type"] for d in r["diffs"]), ["extra_row", "missing_row"])

    def test_header_mismatch(self):
        r = self.cmp("id,v\n1,a\n", "id,w\n1,a\n")
        self.assertFalse(r["passed"])
        self.assertEqual(r["diffs"][0]["type"], "header")

    def test_row_count_difference_positional(self):
        self.assertFalse(self.cmp("id\n1\n2\n", "id\n1\n")["passed"])

    def test_bad_key_column(self):
        with self.assertRaises(core.UserError):
            self.cmp("id\n1\n", "id\n1\n", key_columns=["nope"])


class ServerTests(unittest.TestCase):
    """Starts the real server on a temp copy of the project and drives it over HTTP."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "jobs", cls.tmp / "jobs")
        core.ROOT, app.core.ROOT = ROOT, ROOT
        app.CFG = core.Settings({"JOBS_DIR": str(cls.tmp / "jobs")})
        app.CFG.audit_file = cls.tmp / "audit.csv"
        app.CFG.jobs_dir = cls.tmp / "jobs"
        cls.server = HTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.tmp)

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                text = r.read().decode()
                return r.status, (json.loads(text) if r.headers["Content-Type"].startswith("application/json") else text)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_page_loads(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("BMO ParityPilot", r.read().decode())

    def test_run_blocked_until_approved(self):
        self.call("POST", "/api/jobs", {"name": "gate_job"})
        self.call("POST", "/api/job/gate_job/file", {"kind": "new_job", "content": "print('hi')"})
        self.call("POST", "/api/job/gate_job/file", {"kind": "input", "content": "a\n1\n"})
        status, body = self.call("POST", "/api/job/gate_job/run_modern", {})
        self.assertEqual(status, 400)
        self.assertIn("sign off", body["error"].lower())

    def full_flow(self, job, run_legacy):
        if run_legacy:
            self.assertEqual(self.call("POST", "/api/job/%s/run_legacy" % job, {})[0], 200)
        self.assertEqual(self.call("POST", "/api/job/%s/approve" % job, {"artifact": "new_job", "decision": "approve", "reviewer": "tester", "comment": "ok"})[0], 200)
        self.assertEqual(self.call("POST", "/api/job/%s/run_modern" % job, {})[0], 200)
        return self.call("POST", "/api/job/%s/check" % job, {})[1]

    def test_python_pass(self):
        self.assertTrue(self.full_flow("demo_python_pass", True)["passed"])

    def test_python_fail_shows_float_quirk(self):
        r = self.full_flow("demo_python_fail", True)
        self.assertFalse(r["passed"])
        self.assertTrue(any(d["column"] == "total_cad" for d in r["diffs"]))

    def test_sas_capture_pass(self):
        r = self.full_flow("demo_sas_capture", False)
        self.assertTrue(r["passed"])
        self.assertEqual(r["preview"]["header"], ["account_id", "txn_count", "total_cad"])
        self.assertEqual(len(r["preview"]["expected"]), 8)
        self.assertEqual(r["preview"]["expected"], r["preview"]["actual"])

    def test_editing_code_removes_approval(self):
        job = "demo_sas_capture"
        self.call("POST", "/api/job/%s/approve" % job, {"artifact": "new_job", "decision": "approve", "reviewer": "t", "comment": ""})
        self.assertTrue(self.call("GET", "/api/job/%s" % job)[1]["code_approved"])
        code = self.call("GET", "/api/job/%s" % job)[1]["new_job"]
        self.call("POST", "/api/job/%s/file" % job, {"kind": "new_job", "content": code + "\n# edited\n"})
        self.assertFalse(self.call("GET", "/api/job/%s" % job)[1]["code_approved"])
        self.assertEqual(self.call("POST", "/api/job/%s/run_modern" % job, {})[0], 400)

    def test_no_baseline_means_no_check(self):
        self.call("POST", "/api/jobs", {"name": "empty_job"})
        status, body = self.call("POST", "/api/job/empty_job/check", {})
        self.assertEqual(status, 400)
        self.assertIn("No legacy output", body["error"])

    def test_script_only_generated_input_is_labelled_sample(self):
        job = "scriptonly"
        self.call("POST", "/api/jobs", {"name": job})
        self.call("POST", "/api/job/%s/file" % job, {"kind": "source", "filename": "old.py", "content": "print(1)"})
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "gen_input", "content": "```\ntxn_id,account_id,amount,currency\nT1,A,1.00,CAD\n```"})
        self.call("POST", "/api/job/%s/use_generated_input" % job, {})
        state = self.call("GET", "/api/job/%s" % job)[1]
        self.assertEqual(state["meta"]["input_origin"], "generated")
        self.assertTrue(state["has_input"])

    def test_predicted_output_is_labelled_unverified(self):
        job = "predicted"
        self.call("POST", "/api/jobs", {"name": job})
        self.call("POST", "/api/job/%s/file" % job, {"kind": "source", "filename": "old.py", "content": "print(1)"})
        self.assertEqual(self.call("GET", "/api/job/%s/prompt?step=gen_expected" % job)[0], 400)   # needs input first
        self.call("POST", "/api/job/%s/file" % job, {"kind": "input", "content": "a,b\n1,2\n"})
        status, body = self.call("GET", "/api/job/%s/prompt?step=gen_expected" % job)
        self.assertIn("1,2", body["prompt"])
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "gen_expected", "content": "```\na,b\n1,2\n```"})
        self.call("POST", "/api/job/%s/use_generated_expected" % job, {})
        self.call("POST", "/api/job/%s/file" % job, {"kind": "new_job", "content": "import shutil,sys\nshutil.copy(sys.argv[1], sys.argv[2])\n"})
        self.call("POST", "/api/job/%s/approve" % job, {"artifact": "new_job", "decision": "approve", "reviewer": "t", "comment": ""})
        self.call("POST", "/api/job/%s/run_modern" % job, {})
        r = self.call("POST", "/api/job/%s/check" % job, {})[1]
        self.assertTrue(r["passed"])
        self.assertTrue(r["unverified"])
        self.assertIn("UNVERIFIED", r["data_label"])
        self.assertIn("not proof of parity", self.call("GET", "/api/job/%s/report" % job)[1])

    def test_prompt_built_and_report(self):
        status, body = self.call("GET", "/api/job/demo_python_pass/prompt?step=analyze")
        self.assertEqual(status, 200)
        self.assertIn("legacy_etl.py", body["prompt"])
        status, text = self.call("GET", "/api/job/demo_python_pass/report")
        self.assertIn("Evidence report", text)

    def test_report_page_static_files_and_data(self):
        for path, marker in (("/report?job=demo_python_pass", "Evidence report"), ("/ui/style.css", "--blue"), ("/ui/md.js", "function md")):
            with urllib.request.urlopen(self.base + path) as r:
                self.assertIn(marker, r.read().decode())
        for bad in ("/ui/..%2fcore.py", "/ui/core.py", "/ui/nothere.css"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(self.base + bad)
            self.assertEqual(ctx.exception.code, 404)
        self.full_flow("demo_sas_capture", False)
        self.call("POST", "/api/job/demo_sas_capture/approve", {"artifact": "results", "decision": "approve", "reviewer": "Pat", "comment": "ok"})
        status, data = self.call("GET", "/api/job/demo_sas_capture/report_data")
        self.assertEqual(status, 200)
        self.assertTrue(data["check"]["passed"])
        self.assertEqual(data["results_review"]["reviewer"], "Pat")
        self.assertIn("txn_summary.sas", data["sources"])
        self.assertTrue(data["audit"])

    # ------------------------------------------------------------ new pages and modules
    def raw(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.read(), r.headers

    def test_every_page_is_served(self):
        for path in ("/", "/parity", "/understand", "/mapping-to-code", "/code-to-mapping", "/reconcile", "/report"):
            body, headers = self.raw(path)
            self.assertIn(b"<title>", body, path)
            self.assertTrue(headers["Content-Type"].startswith("text/html"))

    def test_scan_endpoint(self):
        status, data = self.call("GET", "/api/job/demo_python_pass/scan")
        self.assertEqual(status, 200)
        self.assertIn("Python script", data["summary"])
        self.assertEqual(self.call("POST", "/api/jobs", {"name": "noscript"})[0], 200)
        self.assertEqual(self.call("GET", "/api/job/noscript/scan")[0], 400)

    def test_mapping_to_code_flow(self):
        import base64
        job = "m2c"
        self.call("POST", "/api/jobs", {"name": job})
        sample = (ROOT / "samples" / "Source_Target_Mapping_Example.xlsx").read_bytes()
        self.assertEqual(self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "notes.csv", "b64": base64.b64encode(b"a,b").decode()})[0], 400)
        self.assertEqual(self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "x.xlsx", "b64": base64.b64encode(b"not a zip").decode()})[0], 400)
        self.assertEqual(self.call("GET", "/api/job/%s/prompt?step=mapping_sql" % job)[0], 400)          # no mapping yet
        status, state = self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "Source_Target_Mapping_Example.xlsx", "b64": base64.b64encode(sample).decode()})
        self.assertEqual(status, 200)
        self.assertEqual(state["summary"]["columns"], 17)
        self.assertEqual(state["reference_coverage"]["counts"]["missing"], 4)
        status, body = self.call("GET", "/api/job/%s/prompt?step=mapping_sql&dialect=PostgreSQL" % job)
        self.assertIn("PostgreSQL", body["prompt"])
        self.assertIn("M06", body["prompt"])
        self.assertIn("13. [Dedup]", body["prompt"])                         # rules are listed in order
        reply = "Here:\n```sql\nINSERT INTO DWH.FACT_ACCOUNT_DAILY (ACCOUNT_ID) SELECT a.ACCT_ID AS ACCOUNT_ID FROM CBS.DAILY_BALANCE db JOIN CBS.ACCOUNT a ON db.ACCT_ID = a.ACCT_ID;\n```\n## Assumptions and open questions\n- none\n"
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "mapping_sql", "content": reply})
        status, state = self.call("POST", "/api/job/%s/save_generated" % job, {"target": "sql"})
        self.assertEqual(status, 200)
        gen = state["generated"]["sql"]
        self.assertTrue(gen["code"].startswith("INSERT INTO"))
        self.assertGreater(gen["coverage"]["counts"]["missing"], 0)      # one column of 17 is far from complete
        self.assertFalse(gen["approved"])
        self.assertEqual(self.call("POST", "/api/job/%s/approve" % job, {"artifact": "generated_sql", "decision": "approve", "reviewer": "Pat", "comment": ""})[0], 200)
        self.assertTrue(self.call("GET", "/api/job/%s/mapping" % job)[1]["generated"]["sql"]["approved"])
        body, headers = self.raw("/api/job/%s/download?what=sql" % job)
        self.assertIn(b"INSERT INTO", body)
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_unclear_upload_waits_for_review_then_is_saved_and_remembered(self):
        import base64
        import sys
        sys.path.insert(0, str(ROOT / "tests"))
        from test_schema import make_xlsx, sample_grids
        job = "review"
        self.call("POST", "/api/jobs", {"name": job})
        g = sample_grids()
        g["Source_Target_Mapping"][3] = ["Ref", "Sys", "Sch", "Tbl", "Fld", "Dt", "TSch", "TTbl", "TFld", "TDt", "K", "Xform", "N?", "Cmt"]
        data = make_xlsx(g)
        up = lambda: self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "odd.xlsx", "b64": base64.b64encode(data).decode(), "note": "odd headers"})
        status, state = up()
        self.assertEqual(status, 200)
        self.assertIsNone(state["model"])                                   # nothing saved yet
        self.assertTrue(state["upload_result"]["pending"])
        self.assertIn("mapping table", state["pending"]["error"])
        self.assertFalse(state["pending"]["ok"])
        self.assertIn("Source_Target_Mapping", state["pending"]["preview"])
        # the prompt for Copilot carries the headers and rows, not a rewrite request
        prompt = self.call("GET", "/api/job/%s/prompt?step=resolve_schema" % job)[1]["prompt"]
        for text in ("Allowed field names", "target_column", "TFld", "header_row"):
            self.assertIn(text, prompt)
        # Copilot answers; the tool applies it and checks it
        reply = '```json\n{"mapping": {"sheet": "Source_Target_Mapping", "header_row": 4, "columns": {"A": "id", "B": "source_system", "C": "source_schema", "D": "source_table", "E": "source_column", "F": "source_type", "G": "target_schema", "H": "target_table", "I": "target_column", "J": "target_type", "K": "key", "L": "logic", "M": "nullable", "N": "notes", "Z": "bogus"}}}\n```'
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "resolve_schema", "content": reply})
        status, state = self.call("POST", "/api/job/%s/mapping_resolve" % job, {"use_reply": True})
        self.assertEqual(status, 200)
        self.assertTrue(state["pending"]["ok"], state["pending"]["error"])
        self.assertEqual(state["pending"]["report"]["tables"][0]["rows"], 17)
        status, state = self.call("POST", "/api/job/%s/mapping_confirm" % job, {})
        self.assertEqual((status, state["version"], state["upload_result"]["new"]), (200, 1, True))
        self.assertIsNone(state["pending"])
        self.assertEqual(state["model"]["mappings"][0]["target_column"], "ACCOUNT_ID")
        self.assertEqual(len(state["model"]["rules"]), 15)
        # the same layout next time (a changed file in this migration) needs no review
        g2 = sample_grids()
        g2["Source_Target_Mapping"][3] = list(g["Source_Target_Mapping"][3])
        g2["Source_Target_Mapping"][4][11] = "Direct move; changed"
        status, state = self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "odd_again.xlsx", "b64": base64.b64encode(make_xlsx(g2)).decode()})
        self.assertEqual(status, 200)
        self.assertIsNone(state["pending"], state["pending"] and state["pending"]["error"])
        self.assertEqual((state["model"]["detection"]["by"], state["version"]), ("remembered", 2))

    def test_bad_copilot_schema_reply_is_a_clear_error_and_discard_works(self):
        import base64
        job = "review3"
        self.call("POST", "/api/jobs", {"name": job})
        data = b"Col1,Col2,Col3\na,b,c\nd,e,f\n"
        status, state = self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "x.csv", "b64": base64.b64encode(data).decode()})
        self.assertTrue(state["pending"])
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "resolve_schema", "content": "sorry, I cannot do that"})
        status, err = self.call("POST", "/api/job/%s/mapping_resolve" % job, {"use_reply": True})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("POST", "/api/job/%s/mapping_confirm" % job, {})[0], 400)       # still cannot be read
        by_hand = {"mapping": {"columns": {"A": "source_column", "B": "target_column"}}}
        status, state = self.call("POST", "/api/job/%s/mapping_resolve" % job, {"overrides": by_hand})
        self.assertTrue(state["pending"]["ok"])
        status, state = self.call("POST", "/api/job/%s/mapping_discard" % job, {})
        self.assertIsNone(state["pending"])

    def test_generated_code_comes_with_unit_tests_and_documentation(self):
        import base64
        import io
        import zipfile
        import mapping
        job = "withtests"
        self.call("POST", "/api/jobs", {"name": job})
        data = (ROOT / "samples" / "Source_Target_Mapping_Example.xlsx").read_bytes()
        self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "m.xlsx", "b64": base64.b64encode(data).decode()})
        model = mapping.load_xlsx(data)
        tests = "import duckdb\n\n" + "\n".join('def test_%s_x():\n    """%s"""\n    assert duckdb.connect().execute("select 1").fetchone()[0] == 1\n' % (m["id"], m["id"]) for m in model["mappings"][:10])
        reply = "```sql\nINSERT INTO T (ACCOUNT_ID) SELECT a.ACCT_ID AS ACCOUNT_ID FROM CBS.ACCOUNT a -- M01\n```\n\n## Unit tests\n\n```python\n" + tests + "```\n\n## Assumptions and open questions\n- FX rate is daily.\n"
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "mapping_sql", "content": reply})
        status, state = self.call("POST", "/api/job/%s/save_generated" % job, {"target": "sql"})
        self.assertEqual(status, 200)
        g = state["generated"]["sql"]
        self.assertNotIn("def test_", g["code"])                                     # the code is only the code
        self.assertTrue(g["tests"]["check"]["parses"])
        self.assertEqual(g["tests"]["check"]["counts"]["tested"], 10)
        self.assertEqual(g["tests"]["check"]["counts"]["missing"], g["tests"]["check"]["total"] - 10)
        self.assertTrue(state["versions"][0]["tests"]["sql"])
        self.assertIn("FX rate is daily.", state["documentation"]["sql"])
        self.assertIn("## 8. Unit tests", state["documentation"]["sql"])
        # downloads: tests alone, documentation alone, and one package that holds everything
        body, headers = self.raw("/api/job/%s/download?what=tests_sql" % job)
        self.assertIn(b"def test_M01_x", body)
        body, _ = self.raw("/api/job/%s/download?what=doc" % job)
        self.assertIn(b"# withtests", body)
        body, headers = self.raw("/api/job/%s/download?what=package_sql" % job)
        z = zipfile.ZipFile(io.BytesIO(body))
        self.assertEqual(sorted(z.namelist()), ["README.txt", "documentation.md", "generated.sql", "mapping.xlsx", "test_generated_sql.py"])
        self.assertNotIn(b"def test_", z.read("generated.sql"))
        self.assertEqual(self.call("GET", "/api/job/%s/doc" % job)[1]["markdown"][:2], "# ")
        # a code reply without tests removes the old tests instead of keeping stale ones
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "mapping_sql", "content": "```sql\nSELECT 1\n```"})
        status, state = self.call("POST", "/api/job/%s/save_generated" % job, {"target": "sql"})
        self.assertEqual(state["generated"]["sql"]["tests"]["code"], "")
        self.assertEqual(self.call("GET", "/api/job/%s/download?what=tests_sql" % job)[0], 400)
        # new mapping version: version 1 keeps its code and tests, version 2 starts clean
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "mapping_sql", "content": reply})
        self.call("POST", "/api/job/%s/save_generated" % job, {"target": "sql"})
        edited = mapping.load_xlsx(data)
        edited["mappings"][0]["notes"] = "changed"
        status, state = self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "m2.xlsx", "b64": base64.b64encode(mapping.write_xlsx(edited)).decode()})
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["generated"], {})
        self.assertEqual([v["tests"]["sql"] for v in state["versions"]], [True, False])
        body, _ = self.raw("/api/job/%s/download?what=tests_sql&version=1" % job)
        self.assertIn(b"def test_M01_x", body)

    def test_mapping_versions_and_change_tracking(self):
        import base64
        import copy
        import mapping
        job = "versions"
        self.call("POST", "/api/jobs", {"name": job})
        v1_bytes = (ROOT / "samples" / "Source_Target_Mapping_Example.xlsx").read_bytes()
        upload = lambda data, note="": self.call("POST", "/api/job/%s/mapping_upload" % job, {"filename": "map.xlsx", "b64": base64.b64encode(data).decode(), "note": note})
        status, state = upload(v1_bytes, "first cut")
        self.assertEqual((status, state["version"], state["upload_result"]["new"]), (200, 1, True))
        self.assertIsNone(state["diff"])
        # work done on v1: code is generated and approved
        sql1 = "INSERT INTO T (A) SELECT x AS A FROM db WHERE d >= :run_date - 3 -- Rule 8\n"
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "mapping_sql", "content": "```sql\n" + sql1 + "```"})
        self.call("POST", "/api/job/%s/save_generated" % job, {"target": "sql"})
        # the same file again: no new version
        status, state = upload(v1_bytes)
        self.assertFalse(state["upload_result"]["new"])
        self.assertEqual(len(state["versions"]), 1)
        # an edited mapping: window 3 -> 7 days, a column removed, a note added
        model = mapping.load_xlsx(v1_bytes)
        edited = copy.deepcopy(model)
        edited["rules"][7]["expr"] = "db.BAL_DATE >= :run_date - 7 AND db.BAL_DATE <= :run_date"
        edited["mappings"] = [m for m in edited["mappings"] if m["id"] != "M17"]
        edited["mappings"][0]["notes"] = "now documented"
        status, state = upload(mapping.write_xlsx(edited), "load window widened to 7 days")
        self.assertEqual((state["version"], state["upload_result"]["new"]), (2, True))
        self.assertEqual(len(state["versions"]), 2)
        self.assertEqual(state["versions"][1]["note"], "load window widened to 7 days")
        counts = state["diff"]["counts"]
        self.assertEqual((counts["rules_changed"], counts["columns_removed"], counts["columns_changed"]), (1, 1, 1))
        self.assertEqual(state["diff"]["rules"]["changed"][0]["affects"], ["BALANCE_DATE"])
        # v1's code is kept with v1; v2 starts clean
        self.assertEqual(state["generated"], {})
        self.assertTrue(state["versions"][0]["code"]["sql"])
        self.assertFalse(state["versions"][1]["code"]["sql"])
        self.assertTrue(state["previous_code"]["sql"])
        body, _ = self.raw("/api/job/%s/download?what=sql&version=1" % job)
        self.assertIn(b":run_date - 3", body)
        # the update prompt carries the previous code and the change list
        prompt = self.call("GET", "/api/job/%s/prompt?step=mapping_sql&update=1" % job)[1]["prompt"]
        for expected in ("UPDATE INSTRUCTIONS", "changed in v2", ":run_date - 3", "Rule 8", "REMOVED column BATCH_ID"):
            self.assertIn(expected, prompt)
        self.assertNotIn("UPDATE INSTRUCTIONS", self.call("GET", "/api/job/%s/prompt?step=mapping_sql" % job)[1]["prompt"])
        # v2 code, then compare versions including the code diff
        sql2 = sql1.replace("- 3", "- 7").replace("Rule 8", "Rule 8 (changed in v2)")
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "mapping_sql", "content": "```sql\n" + sql2 + "```"})
        self.call("POST", "/api/job/%s/save_generated" % job, {"target": "sql"})
        status, cmp = self.call("GET", "/api/job/%s/mapping_compare?from=1&to=2" % job)
        self.assertEqual(status, 200)
        self.assertEqual((cmp["code"]["sql"]["diff"]["added"], cmp["code"]["sql"]["diff"]["removed"]), (1, 1))
        touched = {x["item"]: x["status"] for x in cmp["code"]["sql"]["touched"]}
        self.assertEqual(touched["Rule 8 Load window"], "touched")
        self.assertEqual(touched["Removed column BATCH_ID"], "not_touched")      # the code never changed for the removed column
        self.assertEqual(self.call("GET", "/api/job/%s/mapping_compare?from=1&to=9" % job)[0], 400)
        # version history appears in the report data
        self.assertEqual(len(self.call("GET", "/api/job/%s/report_data" % job)[1]["mapping"]["versions"]), 2)

    def test_code_to_mapping_flow(self):
        import mapping
        job = "c2m"
        self.call("POST", "/api/jobs", {"name": job})
        self.call("POST", "/api/job/%s/file" % job, {"kind": "source", "filename": "load.sql", "content": "INSERT INTO t (id) SELECT id FROM s;"})
        status, body = self.call("GET", "/api/job/%s/prompt?step=code_to_mapping" % job)
        self.assertIn("load.sql", body["prompt"])
        self.assertIn("Tables read", body["prompt"])                         # measured facts are included
        self.assertEqual(self.call("POST", "/api/job/%s/parse_reply" % job, {})[0], 400)
        reply = ("## Source_Target_Mapping\n| Map ID | Source Table | Source Column | Target Table | Target Column | Transformation / Business Logic |\n|---|---|---|---|---|---|\n"
                 "| M01 | S | id | T | id | Direct move |\n## Joins_Filters\n| Seq | Type | Rule Name | Left Table (Alias) | Join Condition / Filter Expression |\n|---|---|---|---|---|\n| 1 | Driver | Base | S (s) | FROM S s |\n")
        self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "code_to_mapping", "content": reply})
        status, state = self.call("POST", "/api/job/%s/parse_reply" % job, {})
        self.assertEqual(status, 200)
        self.assertEqual(state["summary"]["columns"], 1)
        self.assertEqual(state["coverage"]["counts"]["missing"], 0)          # id and s both appear in the code
        data, headers = self.raw("/api/job/%s/download?what=mapping_xlsx" % job)
        self.assertEqual(mapping.load_xlsx(data)["mappings"][0]["target_column"], "id")
        self.assertIn(b"Map ID", self.raw("/api/job/%s/download?what=mapping_csv" % job)[0])
        self.assertEqual(self.call("POST", "/api/job/%s/approve" % job, {"artifact": "derived_mapping", "decision": "approve", "reviewer": "Pat", "comment": ""})[0], 200)
        self.assertTrue(self.call("GET", "/api/job/%s/derived" % job)[1]["approved"])

    def test_code_to_mapping_versions(self):
        import mapping
        job = "c2mv"
        self.call("POST", "/api/jobs", {"name": job})
        reply = lambda window, extra="": (
            "## Source_Target_Mapping\n| Map ID | Source Table | Source Column | Target Table | Target Column | Transformation / Business Logic |\n|---|---|---|---|---|---|\n"
            "| M01 | S | id | T | id | Direct move |\n" + extra + "## Joins_Filters\n| Seq | Type | Rule Name | Left Table (Alias) | Join Condition / Filter Expression |\n|---|---|---|---|---|\n"
            "| 1 | Driver | Base | S (s) | FROM S s |\n| 2 | Filter | Window | s | s.d >= :run_date - %d |\n" % window)
        script1 = "INSERT INTO t (id) SELECT id FROM s WHERE d >= :run_date - 3;"
        self.call("POST", "/api/job/%s/file" % job, {"kind": "source", "filename": "load.sql", "content": script1})
        save = lambda text: self.call("POST", "/api/job/%s/file" % job, {"kind": "answer", "filename": "code_to_mapping", "content": text})
        parse = lambda note="": self.call("POST", "/api/job/%s/parse_reply" % job, {"note": note})
        save(reply(3))
        status, state = parse("first cut")
        self.assertEqual((status, state["version"], state["upload_result"]["new"]), (200, 1, True))
        # same reply, same script: nothing new
        status, state = parse()
        self.assertFalse(state["upload_result"]["new"])
        self.assertEqual(len(state["versions"]), 1)
        # the code changes (3 -> 7 days) and Copilot returns an updated mapping with one more column
        self.call("POST", "/api/job/%s/file" % job, {"kind": "source", "filename": "load.sql", "content": script1.replace("- 3", "- 7") + "\n-- widened"})
        save(reply(7, "| M02 | S | name | T | name | Direct move |\n"))
        status, state = parse("window widened")
        self.assertEqual((state["version"], state["upload_result"]["new"]), (2, True))
        self.assertIn("Code changed in 1 file", state["upload_result"]["message"])
        self.assertEqual(state["diff"]["counts"]["columns_added"], 1)
        self.assertEqual(state["diff"]["counts"]["rules_changed"], 1)
        self.assertEqual(state["versions"][1]["note"], "window widened")
        self.assertIn("Code changed", state["versions"][1]["summary"])
        status, cmp = self.call("GET", "/api/job/%s/derived_compare?from=1&to=2" % job)
        self.assertEqual(status, 200)
        self.assertEqual(cmp["scripts"]["load.sql"]["status"], "changed")
        self.assertTrue(any("- 7" in l["text"] for l in cmp["scripts"]["load.sql"]["diff"]["lines"] if l["kind"] == "add"))
        # an older version can still be downloaded as Excel
        data, _ = self.raw("/api/job/%s/download?what=mapping_xlsx&version=1" % job)
        self.assertEqual(len(mapping.load_xlsx(data)["mappings"]), 1)
        data, _ = self.raw("/api/job/%s/download?what=mapping_xlsx" % job)
        self.assertEqual(len(mapping.load_xlsx(data)["mappings"]), 2)
        self.assertEqual(self.call("GET", "/api/job/%s/derived_compare?from=1&to=5" % job)[0], 400)
        # approval belongs to one version: approving v2, then changing the mapping, leaves the newest unapproved
        self.call("POST", "/api/job/%s/approve" % job, {"artifact": "derived_mapping", "decision": "approve", "reviewer": "Pat", "comment": ""})
        self.assertTrue(self.call("GET", "/api/job/%s/derived" % job)[1]["versions"][1]["approved"])
        self.assertFalse(self.call("GET", "/api/job/%s/derived" % job)[1]["versions"][0]["approved"])

    def test_report_includes_new_sections(self):
        data = self.call("GET", "/api/job/c2m/report_data")[1] if self.call("GET", "/api/job/c2m")[0] == 200 else {}
        if data:
            self.assertIsNotNone(data["scan"])

    def test_reconcile_endpoint(self):
        status, r = self.call("POST", "/api/reconcile", {"expected": "id,v\n1,a\n2,b\n", "actual": "id,v\n2,b\n1,a\n", "keys": "id", "tolerance": "0"})
        self.assertTrue(r["passed"])
        status, r = self.call("POST", "/api/reconcile", {"expected": "id,v\n1,a\n", "actual": "id,v\n1,z\n"})
        self.assertFalse(r["passed"])
        self.assertEqual(r["diffs"][0]["expected"], "a")
        self.assertEqual(self.call("POST", "/api/reconcile", {"expected": "", "actual": "x"})[0], 400)

    def test_rejects_bad_names_and_cross_site(self):
        self.assertEqual(self.call("POST", "/api/jobs", {"name": "../evil"})[0], 400)
        req = urllib.request.Request(self.base + "/api/jobs", method="POST", data=b'{"name":"x1"}', headers={"Origin": "http://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
