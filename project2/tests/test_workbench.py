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
            self.assertIn("Parity Workbench", r.read().decode())

    def test_run_blocked_until_approved(self):
        self.call("POST", "/api/jobs", {"name": "gate_job"})
        self.call("POST", "/api/job/gate_job/file", {"kind": "new_job", "content": "print('hi')"})
        self.call("POST", "/api/job/gate_job/file", {"kind": "input", "content": "a\n1\n"})
        status, body = self.call("POST", "/api/job/gate_job/run_modern", {})
        self.assertEqual(status, 400)
        self.assertIn("approve", body["error"].lower())

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
        self.assertIn("Parity report", text)

    def test_rejects_bad_names_and_cross_site(self):
        self.assertEqual(self.call("POST", "/api/jobs", {"name": "../evil"})[0], 400)
        req = urllib.request.Request(self.base + "/api/jobs", method="POST", data=b'{"name":"x1"}', headers={"Origin": "http://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
