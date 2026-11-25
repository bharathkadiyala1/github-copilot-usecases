"""Writes a PySpark job (Delta Lake or Iceberg) from the same settings as the pipeline.

IMPORTANT: this machine may have no Spark. The job is generated as plain text and only
RUN when RUN_SPARK=true AND pyspark and Java are installed. Without Spark you still get the
code to review, and the stdlib pipeline proves the logic.
"""
import importlib.util
import logging
import shutil
import subprocess
import sys
from string import Template

from modern.extract import LAYOUT

log = logging.getLogger("workbench")

# Common part of both jobs. $names are filled in from the settings.
CORE = '''
import csv
import os
from pyspark.sql import Window, functions as F

INPUT_PATH = r"$input_path"
INPUT_FORMAT = "$input_format"
OUT_DIR = r"$out_dir"
SPARK_DATE_FORMAT = "$spark_date_format"
FX = "$fx"
ATM_THRESHOLD, ATM_FEE = $atm_threshold, $atm_fee
WIRE_RATE, WIRE_MIN, WIRE_MAX = "$wire_rate", $wire_min, $wire_max
ROUNDING = "$rounding"
CREDIT_TYPES = $credit_types
ALL_TYPES = $all_types
CURRENCIES = $currencies
LAYOUT = $layout


def rounded(column):
    """Round a decimal column to a whole number using the legacy system's rounding style."""
    if ROUNDING == "ROUND_HALF_UP":
        return F.round(column, 0).cast("long")
    if ROUNDING == "ROUND_HALF_EVEN":
        return F.bround(column, 0).cast("long")
    return F.floor(column).cast("long")          # ROUND_DOWN (amounts are positive here)


def read_input(spark):
    if INPUT_FORMAT == "fixed":
        raw = spark.read.text(INPUT_PATH).withColumn("line_no", F.monotonically_increasing_id() + 1)
        columns = [F.trim(F.substring("value", start + 1, end - start)).alias(name) for name, start, end in LAYOUT]
        return raw.select("line_no", *columns)
    raw = spark.read.option("header", True).csv(INPUT_PATH).withColumn("line_no", F.monotonically_increasing_id() + 1)
    return raw.select("line_no", *[F.trim(F.coalesce(F.col(name), F.lit(""))).alias(name) for name, _, _ in LAYOUT])


def transform(df):
    day = F.to_date("txn_date", SPARK_DATE_FORMAT)
    date_ok = day.isNotNull() & (F.date_format(day, SPARK_DATE_FORMAT) == F.col("txn_date"))
    amount_ok = F.col("amount").rlike(r"^[0-9]+(\\.[0-9]{1,2})?$$")
    cents = F.when(amount_ok, F.round(F.col("amount").cast("decimal(20,2)") * 100).cast("long"))
    reason = (F.when(F.col("txn_id") == "", "MISSING_ID")
              .when(F.col("account_id") == "", "BAD_ACCOUNT")
              .when(~date_ok, "BAD_DATE")
              .when(~F.col("txn_type").isin(ALL_TYPES), "BAD_TYPE")
              .when(~amount_ok | (cents <= 0), "BAD_AMOUNT")
              .when(~F.col("currency").isin(CURRENCIES), "BAD_CURRENCY"))
    df = (df.withColumn("reason", reason).withColumn("day", day).withColumn("cents", cents)
            .withColumn("channel", F.when(F.col("channel") == "", "UNKNOWN").otherwise(F.upper("channel"))))

    good = df.filter(F.col("reason").isNull())
    good = good.withColumn("rn", F.row_number().over(Window.partitionBy("txn_id").orderBy("line_no")))
    rejects = (df.filter(F.col("reason").isNotNull()).select("line_no", "txn_id", "reason")
                 .unionByName(good.filter("rn > 1").select("line_no", "txn_id", F.lit("DUPLICATE").alias("reason"))))

    good = good.filter("rn = 1")
    in_cad = rounded(F.col("cents").cast("decimal(30,4)") * F.lit(FX).cast("decimal(10,4)"))
    good = good.withColumn("cad", F.when(F.col("currency") == "CAD", F.col("cents")).otherwise(in_cad))
    wire = rounded(F.col("cad").cast("decimal(30,6)") * F.lit(WIRE_RATE).cast("decimal(10,6)"))
    fee = (F.when((F.col("txn_type") == "WDR") & (F.col("channel") == "ATM") & (F.col("cad") > ATM_THRESHOLD), F.lit(ATM_FEE))
            .when(F.col("txn_type") == "WIRE", F.least(F.greatest(wire, F.lit(WIRE_MIN)), F.lit(WIRE_MAX)))
            .otherwise(F.lit(0)))
    good = good.withColumn("fee", fee).withColumn("credit", F.col("txn_type").isin(CREDIT_TYPES))
    month_end = F.date_add(F.col("day"), 1).substr(9, 2) == "01"

    summary = good.groupBy("account_id").agg(
        F.count("*").alias("txn_count"),
        F.sum(F.when(F.col("credit"), F.col("cad")).otherwise(0)).alias("credits"),
        F.sum(F.when(~F.col("credit"), F.col("cad")).otherwise(0)).alias("debits"),
        F.sum("fee").alias("fees"),
        F.max("day").alias("last_day"),
        F.sum(F.when(month_end, 1).otherwise(0)).alias("month_end_txn_count"),
        F.sum(F.when(F.col("channel") == "ATM", 1).otherwise(0)).alias("atm_txn_count"))
    return summary, rejects


def money(cents):
    return ("-" if cents < 0 else "") + "%d.%02d" % (abs(cents) // 100, abs(cents) % 100)


def write_csv_copy(summary, rejects):
    """Small result tables are also saved as CSV so the parity check can compare them."""
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "summary.csv"), "w", newline="", encoding="utf-8") as out:
        out.write("account_id,txn_count,total_credits_cad,total_debits_cad,total_fees_cad,net_cad,last_txn_date,month_end_txn_count,atm_txn_count\\n")
        for r in summary.orderBy("account_id").collect():
            net = r["credits"] - r["debits"] - r["fees"]
            out.write(",".join([r["account_id"], str(r["txn_count"]), money(r["credits"]), money(r["debits"]), money(r["fees"]),
                                money(net), r["last_day"].isoformat(), str(r["month_end_txn_count"]), str(r["atm_txn_count"])]) + "\\n")
    with open(os.path.join(OUT_DIR, "rejects.csv"), "w", newline="", encoding="utf-8") as out:
        out.write("line_no,txn_id,reason\\n")
        for r in rejects.orderBy("line_no").collect():
            out.write("%d,%s,%s\\n" % (r["line_no"], r["txn_id"], r["reason"]))
'''

DELTA = '''
# ---- Delta Lake target ----
# Needs: pip package delta-spark (or the Delta jars on the Spark classpath).
from pyspark.sql import SparkSession

builder = (SparkSession.builder.appName("txn_summary_delta")
           .config("spark.sql.ansi.enabled", "false")
           .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
           .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"))
try:
    from delta import configure_spark_with_delta_pip
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
except ImportError:
    spark = builder.getOrCreate()                  # jars must already be on the classpath

summary, rejects = transform(read_input(spark))
summary.write.format("delta").mode("overwrite").save(os.path.join(OUT_DIR, "delta_summary"))
rejects.write.format("delta").mode("overwrite").save(os.path.join(OUT_DIR, "delta_rejects"))
write_csv_copy(summary, rejects)
print("delta job done")
'''

ICEBERG = '''
# ---- Iceberg target ----
# Needs the Iceberg Spark runtime jar. Put its path in SPARK_JARS in .env if there is no internet.
from pyspark.sql import SparkSession

JARS = r"$spark_jars"
builder = (SparkSession.builder.appName("txn_summary_iceberg")
           .config("spark.sql.ansi.enabled", "false")
           .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
           .config("spark.sql.catalog.local", "org.apache.iceberg.spark.SparkCatalog")
           .config("spark.sql.catalog.local.type", "hadoop")
           .config("spark.sql.catalog.local.warehouse", os.path.join(OUT_DIR, "iceberg_warehouse")))
if JARS:
    builder = builder.config("spark.jars", JARS)
spark = builder.getOrCreate()

summary, rejects = transform(read_input(spark))
spark.sql("CREATE NAMESPACE IF NOT EXISTS local.db")
summary.writeTo("local.db.txn_summary").using("iceberg").createOrReplace()
rejects.writeTo("local.db.txn_rejects").using("iceberg").createOrReplace()
write_csv_copy(summary, rejects)
print("iceberg job done")
'''


def render_job(cfg, target):
    """Return the PySpark source text for target 'delta' or 'iceberg'."""
    spark_format = cfg.date_format.replace("%Y", "yyyy").replace("%m", "MM").replace("%d", "dd")
    values = dict(
        input_path=str(cfg.input_path), input_format=cfg.input_format,
        out_dir=str(cfg.output_dir / target / cfg.system), spark_date_format=spark_format,
        fx=str(cfg.fx), atm_threshold=cfg.atm_threshold_cents, atm_fee=cfg.atm_fee_cents,
        wire_rate=str(cfg.wire_rate), wire_min=cfg.wire_min_cents, wire_max=cfg.wire_max_cents,
        rounding=cfg.rounding, credit_types=sorted(cfg.credit_types),
        all_types=sorted(cfg.credit_types | cfg.debit_types), currencies=sorted(cfg.currencies),
        layout=LAYOUT, spark_jars=cfg.spark_jars)
    header = ('# GENERATED by modern/spark_codegen.py for legacy system "%s" - do not edit by hand.\n'
              '# NOTE: generated without a Spark install; review it before using it on a real cluster.\n' % cfg.system)
    body = CORE + (DELTA if target == "delta" else ICEBERG)
    return header + Template(body).substitute(values)


def spark_available():
    return importlib.util.find_spec("pyspark") is not None and shutil.which("java") is not None


def generate_and_maybe_run(cfg):
    """Write generated/<system>_<target>_job.py. Run it only if allowed and possible."""
    cfg.generated_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.generated_dir / ("%s_%s_job.py" % (cfg.system, cfg.target))
    path.write_text(render_job(cfg, cfg.target), encoding="utf-8")
    log.info("generated PySpark job: %s", path)
    if not cfg.run_spark:
        return {"job": str(path), "ran": False, "note": "RUN_SPARK=false"}
    if not spark_available():
        return {"job": str(path), "ran": False, "note": "pyspark or java not found"}
    result = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    log.info("spark job exit code %s", result.returncode)
    return {"job": str(path), "ran": True, "exit_code": result.returncode, "tail": (result.stdout + result.stderr)[-400:]}
