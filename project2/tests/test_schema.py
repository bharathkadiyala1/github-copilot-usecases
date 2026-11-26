"""Resilient reading: different headers, order, tab names, layouts and formats must give the same model as the sample.

Run:  python -m unittest discover -s tests -v
"""
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mapping
import schema

SAMPLE = ROOT / "samples" / "Source_Target_Mapping_Example.xlsx"
MAP_KEYS = ("id", "source_schema", "source_table", "source_column", "target_schema", "target_table", "target_column", "key", "logic")
RULE_KEYS = ("seq", "type", "left", "right", "join_type", "expr", "applies")


def sample_grids():
    g = mapping.read_xlsx(SAMPLE.read_bytes())
    maps, rules = g["Source_Target_Mapping"], g["Joins_Filters"]
    rules = [r for r in rules if not (r and r[0].startswith(("INSERT", "Reference")))]
    return {"Source_Target_Mapping": [list(r) for r in maps], "Joins_Filters": [list(r) for r in rules]}


def rename(grids, sheet, mapping_of, header_row=3):
    """Rename header cells in one sheet. Cells not in mapping_of are kept."""
    out = {k: [list(r) for r in v] for k, v in grids.items()}
    row = out[sheet][header_row]
    out[sheet][header_row] = [mapping_of.get(c, c) for c in row]
    return out


def project(model, keys_map=MAP_KEYS, keys_rule=RULE_KEYS):
    return ([{k: m[k] for k in keys_map} for m in model["mappings"]], [{k: r[k] for k in keys_rule} for r in model["rules"]])


def make_xlsx(sheets, merges=None):
    """A minimal workbook writer with merged-cell support, so the tests do not depend on the app's own writer."""
    merges = merges or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        names = list(sheets)
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   + "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(names)))
                   + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>' % (
            mapping.NS["m"], mapping.NS["r"], "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n), i + 1, i + 1) for i, n in enumerate(names))))
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1) for i in range(len(names)))
                   + "</Relationships>")
        for i, name in enumerate(names):
            rows = []
            for r, row in enumerate(sheets[name], start=1):
                cells = "".join('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (schema._col_letter(c), r, escape(v)) for c, v in enumerate(row) if v != "")
                if cells:
                    rows.append('<row r="%d">%s</row>' % (r, cells))
            merge = "".join('<mergeCell ref="%s"/>' % m for m in merges.get(name, []))
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), '<?xml version="1.0"?><worksheet xmlns="%s"><sheetData>%s</sheetData>%s</worksheet>' % (
                mapping.NS["m"], "".join(rows), ('<mergeCells count="%d">%s</mergeCells>' % (len(merges[name]), merge)) if merge else ""))
    return buf.getvalue()


class ResilientReading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = sample_grids()
        cls.want = project(mapping.parse_workbook(cls.base))

    def same(self, grids, msg=""):
        got = project(mapping.parse_workbook(grids))
        self.assertEqual(got[0], self.want[0], msg)
        self.assertEqual(got[1], self.want[1], msg)

    # --- headers
    def test_the_sample_itself_is_read_with_full_confidence(self):
        report = mapping.parse_workbook(self.base)["detection"]
        self.assertFalse(report["needs_review"], report["warnings"])
        self.assertTrue(all(c["level"] == "high" for t in report["tables"] for c in t["columns"] if c["field"]))
        self.assertEqual(report["rows_in"], report["rows_out"])

    def test_different_wording_on_both_tabs(self):
        g = rename(self.base, "Source_Target_Mapping", {
            "Map ID": "Mapping Ref", "Source System": "Src System", "Source Schema": "SRC_SCHEMA", "Source Table": "Source Entity",
            "Source Column": "Source Field Name", "Source Data Type": "Src Datatype", "Target Schema": "Tgt Schema", "Target Table": "Target Table Name",
            "Target Column": "Target Attribute", "Target Data Type": "Tgt Dtype", "Key": "PK/FK", "Transformation / Business Logic": "Transformation Rule",
            "Nullable": "Null?", "Notes": "Remarks"})
        g = rename(g, "Joins_Filters", {"Seq": "Step No", "Type": "Rule Type", "Rule Name": "Name", "Left Table (Alias)": "Left Side",
                                        "Right Table (Alias)": "Join Table", "Join Type": "Join Kind", "Join Condition / Filter Expression": "Condition",
                                        "Business Logic / Rationale": "Reason", "Applies To Map IDs": "Columns Affected"})
        self.same(g)

    def test_snake_case_and_camel_case_headers(self):
        g = rename(self.base, "Source_Target_Mapping", {"Source Schema": "source_schema", "Source Table": "sourceTable", "Source Column": "SRC_COL",
                                                         "Target Table": "targetTable", "Target Column": "tgt_column", "Transformation / Business Logic": "transformationLogic"})
        self.same(g)

    def test_typos_are_read_but_flagged_for_review(self):
        g = rename(self.base, "Source_Target_Mapping", {"Source Column": "Sorce Colum", "Target Column": "Targt Column"})
        model = mapping.parse_workbook(g)
        self.same(g)
        self.assertTrue(model["detection"]["needs_review"])

    def test_extra_and_reordered_columns(self):
        g = {k: [list(r) for r in v] for k, v in self.base.items()}
        rows = g["Source_Target_Mapping"]
        order = [8, 4, 3, 2, 0, 1, 5, 6, 7, 9, 10, 11, 12, 13]
        g["Source_Target_Mapping"] = [[r[i] if i < len(r) else "" for i in order] + (["Owner: Finance", "extra"][:1] if n == 3 else ["bob"] if n > 3 else []) for n, r in enumerate(rows)]
        g["Source_Target_Mapping"][3][-1] = "Reviewed By"
        model = mapping.parse_workbook(g)
        self.same(g)
        self.assertEqual(model["extras"]["mappings"][0]["Reviewed By"], "bob")        # nothing is thrown away

    def test_header_row_can_be_anywhere_with_title_rows_above(self):
        g = {k: [[""], ["Quarterly review"], ["Prepared by someone"], [""], [""]] + [list(r) for r in v[2:]] for k, v in self.base.items()}
        self.same(g)

    # --- layout
    def test_tabs_swapped_and_renamed(self):
        g = {"Rules": self.base["Joins_Filters"], "Sheet1": self.base["Source_Target_Mapping"]}
        self.same(g)

    def test_both_tables_on_one_sheet(self):
        one = [list(r) for r in self.base["Source_Target_Mapping"]] + [[], [], ["Joins and filters"]] + [list(r) for r in self.base["Joins_Filters"][2:]]
        model = mapping.parse_workbook({"Everything": one})
        self.assertEqual(project(model), self.want)

    def test_rules_above_mapping_on_one_sheet(self):
        one = [list(r) for r in self.base["Joins_Filters"]] + [[], []] + [list(r) for r in self.base["Source_Target_Mapping"]]
        self.assertEqual(project(mapping.parse_workbook({"Both": one})), self.want)

    def test_two_row_header_with_group_band(self):
        rows = self.base["Source_Target_Mapping"]
        band = ["", "Source", "", "", "", "", "Target", "", "", "", "", "", "", ""]
        short = {"Source System": "System", "Source Schema": "Schema", "Source Table": "Table", "Source Column": "Column", "Source Data Type": "Type",
                 "Target Schema": "Schema", "Target Table": "Table", "Target Column": "Column", "Target Data Type": "Type"}
        head = [short.get(c, c) for c in rows[3]]
        g = {"Source_Target_Mapping": [rows[0], rows[1], rows[2], band, head] + rows[4:], "Joins_Filters": self.base["Joins_Filters"]}
        self.same(g)

    def test_merged_cells_in_a_real_xlsx(self):
        rows = [list(r) for r in self.base["Source_Target_Mapping"]]
        band = ["", "Source", "", "", "", "", "Target", "", "", "", "", "", "", ""]
        short = {"Source System": "System", "Source Schema": "Schema", "Source Table": "Table", "Source Column": "Column", "Source Data Type": "Type",
                 "Target Schema": "Schema", "Target Table": "Table", "Target Column": "Column", "Target Data Type": "Type"}
        rows = rows[:3] + [band, [short.get(c, c) for c in rows[3]]] + rows[4:]
        for r in rows[5:]:
            r[6], r[7] = "DWH", "FACT_ACCOUNT_DAILY"
        merged = [r[:] for r in rows]
        for r in merged[6:]:
            r[6] = r[7] = ""
        data = make_xlsx({"Mapping": merged, "Rules": self.base["Joins_Filters"]},
                         {"Mapping": ["B4:F4", "G4:K4", "G6:G%d" % len(merged), "H6:H%d" % len(merged), "A1:N1"]})
        got = project(mapping.load_xlsx(data))
        self.assertEqual(got[0], self.want[0])                              # target schema/table merged down still reach every row
        self.assertEqual(got[1], self.want[1])

    def test_no_joins_tab_still_loads_with_a_warning(self):
        model = mapping.parse_workbook({"Only": self.base["Source_Target_Mapping"]})
        self.assertEqual(model["rules"], [])
        self.assertTrue(any("joins/filters" in w.lower() for w in model["detection"]["warnings"]))

    def test_no_type_column_in_the_rules_table(self):
        g = {k: [list(r) for r in v] for k, v in self.base.items()}
        g["Joins_Filters"] = [(r[:1] + r[2:]) if r else r for r in g["Joins_Filters"]]
        model = mapping.parse_workbook(g)
        self.assertEqual([r["type"] for r in model["rules"]][:4], ["Driver", "Join", "Join", "Join"])   # worked out from the expression

    # --- values
    def test_rule_type_and_join_wording(self):
        g = {k: [list(r) for r in v] for k, v in self.base.items()}
        rows = g["Joins_Filters"]
        rows[4][1] = "Base Table"
        rows[5][1], rows[5][5] = "Left Outer Join", "-"
        rows[6][1], rows[6][5] = "join", "Left Outer Join"
        model = mapping.parse_workbook(g)
        self.assertEqual([model["rules"][i]["type"] for i in (0, 1, 2)], ["Driver", "Join", "Join"])
        self.assertEqual([model["rules"][i]["join_type"] for i in (1, 2)], ["LEFT", "LEFT"])

    def test_key_nullable_and_seq_wording(self):
        g = {k: [list(r) for r in v] for k, v in self.base.items()}
        g["Source_Target_Mapping"][3][10] = "Primary Key"
        g["Source_Target_Mapping"][3][12] = "NOT NULL"
        g["Joins_Filters"][3][0] = "1.0"
        model = mapping.parse_workbook(g)
        self.assertEqual((model["mappings"][0]["key"], model["mappings"][0]["nullable"], model["rules"][0]["seq"]), ("PK", "N", "1"))

    # --- when it cannot know
    def test_unknown_headers_with_no_clues_ask_for_help_not_a_wrong_answer(self):
        g = {"S": [["Col1", "Col2", "Col3"], ["a", "b", "c"], ["d", "e", "f"]]}
        with self.assertRaises(schema.Unresolved) as cm:
            mapping.parse_workbook(g)
        self.assertIn("mapping table", str(cm.exception))
        self.assertIn("S", cm.exception.grids)

    def test_a_person_or_copilot_can_name_the_columns(self):
        g = {"S": [["Col1", "Col2", "Col3", "Col4"], ["M01", "ACCOUNT", "ACCT_ID", "ACCOUNT_ID"], ["M02", "ACCOUNT", "CUST_ID", "CUSTOMER_ID"]]}
        with self.assertRaises(schema.Unresolved):
            mapping.parse_workbook(g)
        fix = {"mapping": {"sheet": "S", "header_row": 1, "columns": {"A": "id", "B": "source_table", "C": "source_column", "D": "target_column"}}}
        model = mapping.parse_workbook(g, fix)
        self.assertEqual([(m["id"], m["source_column"], m["target_column"]) for m in model["mappings"]], [("M01", "ACCT_ID", "ACCOUNT_ID"), ("M02", "CUST_ID", "CUSTOMER_ID")])

    def test_content_alone_can_identify_a_column(self):
        g = rename(self.base, "Source_Target_Mapping", {"Key": "Misc", "Nullable": "Misc2"})
        model = mapping.parse_workbook(g)
        keys = {c["field"]: c for t in model["detection"]["tables"] for c in t["columns"]}
        self.assertEqual(keys["key"]["how"], "content")
        self.assertIn(keys["key"]["level"], ("medium", "low"))              # found from the values, so a person should confirm
        self.assertTrue(model["detection"]["needs_review"])

    def test_skipped_rows_are_reported(self):
        g = {k: [list(r) for r in v] for k, v in self.base.items()}
        g["Source_Target_Mapping"].append(["", "x", "y", "z"])
        model = mapping.parse_workbook(g)
        self.assertTrue(any("skipped" in w for w in model["detection"]["warnings"]))

    # --- other formats
    def test_csv_text(self):
        rows = self.base["Source_Target_Mapping"][2:]
        text = "\n".join(",".join('"%s"' % c.replace('"', '""') for c in r) for r in rows)
        model = mapping.load_xlsx(text.encode(), filename="map.csv")
        self.assertEqual(project(model)[0], self.want[0])

    def test_markdown_reply_with_different_headers(self):
        reply = """Sure.
| Mapping # | Src Schema | Src Table | Src Column | Tgt Schema | Tgt Table | Tgt Column | Derivation |
|---|---|---|---|---|---|---|---|
| M1 | CBS | ACCOUNT | ACCT_ID | DWH | FACT | ACCOUNT_ID | Direct move |

| Step | Rule Type | Description | Left | Right | Join | Predicate | Applies To |
|---|---|---|---|---|---|---|---|
| 1 | Base Table | Base | CBS.ACCOUNT (a) | - | - | FROM CBS.ACCOUNT a | M1 |
| 2 | WHERE | Only open | - | - | - | a.STATUS = 'A' | M1 |
"""
        model = mapping.model_from_reply(reply)
        self.assertEqual((model["mappings"][0]["id"], model["mappings"][0]["target_column"]), ("M1", "ACCOUNT_ID"))
        self.assertEqual([r["type"] for r in model["rules"]], ["Driver", "Filter"])

    def test_json_reply(self):
        reply = "```json\n" + json.dumps({"mappings": [{"id": "M1", "source_column": "a", "target_column": "b", "logic": "Direct move"}],
                                          "rules": [{"seq": 1, "type": "driver", "expr": "FROM t"}]}) + "\n```"
        model = mapping.model_from_reply(reply)
        self.assertEqual((model["mappings"][0]["target_column"], model["rules"][0]["type"]), ("b", "Driver"))

    def test_garbage_is_a_clear_error(self):
        for bad in ("no tables here", ""):
            with self.assertRaises(ValueError):
                mapping.model_from_reply(bad)
        with self.assertRaises(ValueError):
            mapping.read_any(b"\x00\x01\x02 not a file", "x.xlsx")

    def test_written_workbook_reads_back_exactly(self):
        model = mapping.parse_workbook(self.base)
        again = mapping.load_xlsx(mapping.write_xlsx(model))
        self.assertEqual((again["mappings"], again["rules"]), (model["mappings"], model["rules"]))


class Pieces(unittest.TestCase):
    def test_header_words(self):
        self.assertEqual(schema.tokens("Src_Tbl_Name"), ["source", "table"])
        self.assertEqual(schema.tokens("Source Data Type"), ["source", "type"])
        self.assertEqual(schema.tokens("sourceColumn"), ["source", "column"])

    def test_source_is_never_mistaken_for_target(self):
        for field, header in (("target_column", "Source Column"), ("source_table", "Target Table"), ("type", "Source Data Type")):
            kind = "rules" if field == "type" else "mapping"
            self.assertEqual(schema.header_score(kind, field, schema.tokens(header)), 0.0, header)

    def test_bare_columns_are_split_by_position(self):
        grid = [["Table", "Column", "Table", "Column"], ["A", "B", "C", "D"]]
        t = schema._score_table("mapping", "s", grid, 0)
        self.assertEqual({j: f for j, (f, _, _) in t["assign"].items()}, {0: "source_table", 1: "source_column", 2: "target_table", 3: "target_column"})

    def test_rule_types(self):
        for word, want in (("Base Table", "Driver"), ("WHERE", "Filter"), ("Group By", "Aggregation"), ("De-dup", "Dedup"), ("Upsert", "Target Load"), ("weird", "weird")):
            self.assertEqual(schema.rule_type(word), want)


if __name__ == "__main__":
    unittest.main()
