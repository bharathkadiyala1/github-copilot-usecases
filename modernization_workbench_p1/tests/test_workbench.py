"""Tests. Run from the project folder with:  python -m unittest discover -s tests -v
Only the standard library is used. Test data is written to a temporary folder."""
import ast
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot import client, prompts                      # noqa: E402
from modern import spark_codegen, transform             # noqa: E402
from modern.config import SYSTEMS, Config, read_env_file, ROOT   # noqa: E402
from parity import report                                # noqa: E402
from tools import generate_mock_data, make_golden        # noqa: E402


def temp_config(folder, **extra):
    """A Config whose files all live in `folder`, so tests never touch your real data."""
    join = lambda name: os.path.join(folder, name)
    overrides = {"INPUT_FIXED": join("in.dat"), "INPUT_CSV": join("in.csv"), "OUTPUT_DIR": join("out"),
                 "GOLDEN_DIR": join("golden"), "AUDIT_DIR": join("audit"), "REPORT_DIR": join("reports"),
                 "GENERATED_DIR": join("generated"), "MOCK_ROWS": "300"}
    overrides.update(extra)
    return Config(overrides=overrides)


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def test_amount_parsing(self):
        self.assertEqual(transform.parse_amount_cents("12.5"), 1250)
        self.assertEqual(transform.parse_amount_cents("0012.05"), 1205)
        self.assertEqual(transform.parse_amount_cents("7"), 700)
        for bad in ["ABC", "1.2.3", "12.345", "-5.00", ".50", "5.", ""]:
            self.assertIsNone(transform.parse_amount_cents(bad), bad)

    def test_dates(self):
        self.assertEqual(transform.parse_date("20240229", "%Y%m%d"), date(2024, 2, 29))
        self.assertIsNone(transform.parse_date("20230229", "%Y%m%d"))
        self.assertIsNone(transform.parse_date("2024-1-5", "%Y-%m-%d"))
        self.assertIsNone(transform.parse_date("2024105", "%Y%m%d"))

    def test_usd_rounding_styles(self):
        # 1.10 USD x 1.35 = 1.485 CAD -> the three styles give three kinds of answers
        for system, expected in (("python", 149), ("ssis", 148), ("cobol", 148)):
            cfg = Config(system=system)
            self.assertEqual(transform.to_cad_cents(110, "USD", cfg), expected, system)
        self.assertEqual(transform.to_cad_cents(110, "CAD", self.cfg), 110)

    def test_fees(self):
        cfg = Config(system="python")
        self.assertEqual(transform.fee_cents("WDR", "ATM", 50000, cfg), 0)
        self.assertEqual(transform.fee_cents("WDR", "ATM", 50001, cfg), 200)
        self.assertEqual(transform.fee_cents("WDR", "ONLINE", 90000, cfg), 0)
        self.assertEqual(transform.fee_cents("WIRE", "ONLINE", 123456, cfg), 500)      # minimum
        self.assertEqual(transform.fee_cents("WIRE", "ONLINE", 5000050, cfg), 2500)    # maximum
        self.assertEqual(transform.fee_cents("WIRE", "ONLINE", 1200500, cfg), 1201)    # tie, half up
        self.assertEqual(transform.fee_cents("WIRE", "ONLINE", 1200500, Config(system="cobol")), 1200)

    def test_reject_reasons_in_order(self):
        cfg = Config(system="python")
        good = {"line_no": 1, "txn_id": "A1", "account_id": "ACC1", "txn_date": "20240102", "txn_type": "DEP",
                "amount": "5.00", "currency": "CAD", "channel": "", "memo": ""}
        cases = [({"txn_id": ""}, "MISSING_ID"), ({"account_id": ""}, "BAD_ACCOUNT"), ({"txn_date": "20230229"}, "BAD_DATE"),
                 ({"txn_type": "XXX"}, "BAD_TYPE"), ({"amount": "0.00"}, "BAD_AMOUNT"), ({"currency": "EUR"}, "BAD_CURRENCY"),
                 ({"txn_id": "A1", "amount": "x", "currency": "EUR"}, "BAD_AMOUNT")]   # first failing check wins
        for change, reason in cases:
            self.assertEqual(transform.validate(dict(good, **change), cfg, set())[0], reason)
        seen = set()
        self.assertIsNone(transform.validate(good, cfg, seen)[0])
        self.assertEqual(transform.validate(good, cfg, seen)[0], "DUPLICATE")

    def test_env_file_parser(self):
        values = read_env_file(ROOT / ".env.example")
        self.assertEqual(values["FX_USD_CAD"], "1.35")
        self.assertEqual(values["ROUNDING_OVERRIDE"], "")      # trailing comments are removed
        self.assertEqual(values["DATE_FORMAT_FIXED"], "%Y%m%d")


class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.cfg = temp_config(cls.folder)
        generate_mock_data.generate(cls.cfg)
        make_golden.make_golden(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_all_five_systems_match_golden(self):
        for result in report.run_all_checks(self.cfg, SYSTEMS):
            self.assertTrue(result["passed"], result)

    def test_wrong_rounding_is_caught(self):
        broken = temp_config(self.folder, ROUNDING_OVERRIDE="ROUND_HALF_EVEN")
        self.assertFalse(report.check_system(broken, "python")["passed"])

    def test_edge_case_rejects_are_present(self):
        text = (self.cfg.golden_dir / "python" / "rejects.csv").read_text(encoding="utf-8")
        for reason in ("MISSING_ID", "BAD_ACCOUNT", "BAD_DATE", "BAD_TYPE", "BAD_AMOUNT", "BAD_CURRENCY", "DUPLICATE"):
            self.assertIn(reason, text)

    def test_independent_simulation_matches_legacy_python(self):
        from tools.simulate_legacy import simulate
        summary, rejects = simulate(self.cfg.input_fixed, "ROUND_HALF_UP")
        golden = self.cfg.golden_dir / "python"
        self.assertEqual(summary, (golden / "summary.csv").read_text(encoding="utf-8"))
        self.assertEqual(rejects, (golden / "rejects.csv").read_text(encoding="utf-8"))

    def test_report_is_written(self):
        path = report.write_report(self.cfg, report.run_all_checks(self.cfg, ["python"]))
        self.assertIn("PASS", path.read_text(encoding="utf-8"))


class CodegenTests(unittest.TestCase):
    def test_generated_pyspark_is_valid_python(self):
        for system in ("python", "sql"):                  # one fixed-width, one csv
            for target in ("delta", "iceberg"):
                source = spark_codegen.render_job(Config(system=system), target)
                ast.parse(source)
                self.assertIn("transform(", source)


class CopilotTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_prompts_contain_the_legacy_source(self):
        for system in SYSTEMS:
            text = prompts.build_prompt("analyze_rules", Config(system=system))
            self.assertIn("### FILE:", text)
            self.assertNotIn("{source}", text)

    def test_paste_mode_waits_and_logs(self):
        cfg = temp_config(self.folder, COPILOT_CMD="")
        status, answer = client.ask(cfg, "analyze_rules", "hello")
        self.assertEqual(status, "WAITING_FOR_PASTE")
        self.assertIsNone(answer)
        self.assertTrue((cfg.audit_dir / "prompt_log.csv").exists())
        (cfg.generated_dir / "copilot_answers" / "python_analyze_rules.md").write_text("reply", encoding="utf-8")
        self.assertEqual(client.ask(cfg, "analyze_rules", "hello")[0], "ANSWER_FOUND")

    def test_command_mode_with_fake_copilot(self):
        fake = '"%s" "%s"' % (sys.executable, os.path.join(str(ROOT), "tools", "fake_copilot.py"))
        cfg = temp_config(self.folder, COPILOT_CMD=fake)
        status, answer = client.ask(cfg, "debt_register", "some prompt")
        self.assertEqual(status, "ANSWER_SAVED")
        self.assertIn("SIMULATED", answer)

    def test_bad_command_is_reported_not_raised(self):
        cfg = temp_config(self.folder, COPILOT_CMD="no_such_command_xyz")
        self.assertTrue(client.ask(cfg, "debt_register", "x")[0].startswith("COMMAND_FAILED"))

    def test_split_command_keeps_placeholders(self):
        self.assertEqual(client.split_command('tool --flag "{prompt_file}"')[-1], "{prompt_file}")


if __name__ == "__main__":
    unittest.main()
