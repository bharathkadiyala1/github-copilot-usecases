"""Step 1 - EXTRACT. Reads the input file and yields one dict of text values per row.

Rows are yielded one at a time (a generator), so a very large file never has to fit in memory.
"""
import csv

# (field name, start, end) for the fixed-width file. Same layout as the COBOL copybook.
LAYOUT = [
    ("txn_id", 0, 8),
    ("account_id", 8, 18),
    ("txn_date", 18, 26),
    ("txn_type", 26, 32),
    ("amount", 32, 44),
    ("currency", 44, 47),
    ("channel", 47, 53),
    ("memo", 53, 72),
]
LINE_WIDTH = 72


def read_rows(cfg):
    if cfg.input_format == "fixed":
        yield from _read_fixed(cfg.input_path)
    else:
        yield from _read_csv(cfg.input_path)


def _read_fixed(path):
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.rstrip("\r\n").ljust(LINE_WIDTH)
            row = {name: line[start:end].strip() for name, start, end in LAYOUT}
            row["line_no"] = line_no
            yield row


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        for line_no, raw in enumerate(csv.DictReader(handle), start=1):
            row = {key: (value or "").strip() for key, value in raw.items()}
            row["line_no"] = line_no
            yield row
