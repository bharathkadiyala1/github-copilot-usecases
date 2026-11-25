"""Reads settings from the .env file.

Plain Python only. A real environment variable with the same name beats the .env file,
and the `overrides` argument beats both (used by tests and the demo).
"""
import decimal
import os
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSTEMS = ["python", "sql", "cobol", "sas", "ssis"]
ROUNDING_MODES = {
    "ROUND_HALF_UP": decimal.ROUND_HALF_UP,
    "ROUND_HALF_EVEN": decimal.ROUND_HALF_EVEN,
    "ROUND_DOWN": decimal.ROUND_DOWN,
}


def read_env_file(path):
    """Return {KEY: value} from a .env file. Comments start with #."""
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.sub(r"\s+#.*$", "", value).strip()   # drop trailing comment
        values[key.strip()] = value.strip('"').strip("'")
    return values


def parse_map(text):
    """'a:b,c:d' -> {'a': 'b', 'c': 'd'}"""
    result = {}
    for pair in text.split(","):
        if ":" in pair:
            key, value = pair.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def to_cents(text):
    """'2.50' -> 250"""
    return int((Decimal(text) * 100).to_integral_value())


class Config:
    def __init__(self, system=None, overrides=None):
        env_file = ROOT / ".env"
        if not env_file.exists():
            env_file = ROOT / ".env.example"
        values = read_env_file(env_file)
        for key in list(values):
            if key in os.environ:
                values[key] = os.environ[key]
        values.update(overrides or {})
        self.values = values
        get = values.get

        self.system = system or get("LEGACY_SYSTEM", "python")
        self.target = get("TARGET", "csv")
        self.run_spark = get("RUN_SPARK", "false").lower() == "true"
        self.spark_jars = get("SPARK_JARS", "")
        self.log_level = get("LOG_LEVEL", "INFO")
        self.log_every = int(get("LOG_EVERY", "100000"))
        self.mock_rows = int(get("MOCK_ROWS", "500"))
        self.mock_seed = int(get("MOCK_SEED", "42"))

        self.input_fixed = ROOT / get("INPUT_FIXED", "data/input/transactions.dat")
        self.input_csv = ROOT / get("INPUT_CSV", "data/input/transactions.csv")
        self.output_dir = ROOT / get("OUTPUT_DIR", "output")
        self.golden_dir = ROOT / get("GOLDEN_DIR", "golden")
        self.report_dir = ROOT / get("REPORT_DIR", "reports")
        self.audit_dir = ROOT / get("AUDIT_DIR", "audit")
        self.generated_dir = ROOT / get("GENERATED_DIR", "generated")

        self.fx = Decimal(get("FX_USD_CAD", "1.35"))
        self.atm_threshold_cents = to_cents(get("ATM_FEE_THRESHOLD", "500.00"))
        self.atm_fee_cents = to_cents(get("ATM_FEE", "2.00"))
        self.wire_rate = Decimal(get("WIRE_FEE_RATE", "0.001"))
        self.wire_min_cents = to_cents(get("WIRE_FEE_MIN", "5.00"))
        self.wire_max_cents = to_cents(get("WIRE_FEE_MAX", "25.00"))
        self.credit_types = set(get("CREDIT_TYPES", "DEP,INT,XFRIN").split(","))
        self.debit_types = set(get("DEBIT_TYPES", "WDR,WIRE,POS,XFROUT").split(","))
        self.currencies = set(get("CURRENCIES", "CAD,USD").split(","))

        rounding_map = parse_map(get("ROUNDING_BY_SYSTEM", ""))
        self.rounding = get("ROUNDING_OVERRIDE", "") or rounding_map.get(self.system, "ROUND_HALF_UP")
        self.rounding_mode = ROUNDING_MODES[self.rounding]
        self.input_format = parse_map(get("INPUT_FORMAT_BY_SYSTEM", "")).get(self.system, "fixed")
        self.date_format_fixed = get("DATE_FORMAT_FIXED", "%Y%m%d")
        self.date_format_csv = get("DATE_FORMAT_CSV", "%Y-%m-%d")

        self.copilot_cmd = get("COPILOT_CMD", "")
        self.copilot_timeout = int(get("COPILOT_TIMEOUT", "180"))
        self.reviewer = get("REVIEWER", "reviewer")

    @property
    def input_path(self):
        return self.input_fixed if self.input_format == "fixed" else self.input_csv

    @property
    def date_format(self):
        return self.date_format_fixed if self.input_format == "fixed" else self.date_format_csv

    def for_system(self, system):
        """A copy of this config switched to another legacy system."""
        return Config(system=system, overrides=self.values)
