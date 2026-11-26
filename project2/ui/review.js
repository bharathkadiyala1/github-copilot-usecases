/* Review screen for an upload the tool could not read with certainty, and the "how was this read" view. Needs common.js + nav.js. */
var esc = (typeof esc === "function") ? esc : escapeHtml;
var KIND_TITLE = { mapping: "Mapping table (one row per target column)", rules: "Joins, filters and rules table" };
function levelPill(c) {
  if (!c.field) return '<span class="pill">not used</span>';
  var cls = c.level === "high" ? "ok" : c.level === "medium" ? "warn" : "bad";
  var how = c.how === "content" ? " (from the values)" : c.how === "chosen" ? " (chosen)" : "";
  return '<span class="pill ' + cls + '" style="margin-left:0">' + c.level + how + "</span>";
}
function detectionHtml(det) {
  if (!det || !det.tables) return "";
  var by = { auto: "Read automatically; every column was recognised clearly.", confirmed: "Columns confirmed by a person.", reply: "Read from the reply, but some headers were not exact. Please check the table below.", remembered: "Same layout as an earlier upload; the confirmed column choices were reused." }[det.by] || "";
  var h = '<p class="hint">' + esc(by) + "</p>";
  det.tables.forEach(function (t) {
    h += "<h4 style=\"margin:12px 0 4px\">" + esc(KIND_TITLE[t.kind]) + ' <span class="hint">sheet ' + esc(t.sheet) + ", header on row " + t.header_row + ", " + t.rows + " row(s)</span></h4>";
    h += table(["Column", "Header in the file", "Read as", "Confidence"], t.columns.map(function (c) {
      return [esc(c.letter), esc(c.header || "(none)"), c.field ? esc(c.label) : '<span class="hint">not used</span>', levelPill(c)];
    }));
  });
  (det.warnings || []).forEach(function (w) { h += '<p class="hint">&bull; ' + esc(w) + "</p>"; });
  return h;
}
function reviewHtml(p) {
  var r = p.report || { tables: [] }, h = "";
  h += '<p>' + (p.error ? "<b>" + esc(p.error) + "</b>" : "The file was read, but some columns are not certain. Check the right-hand choices, then save.") + "</p>";
  h += '<p class="hint">File: <b>' + esc(p.filename) + "</b>. Nothing is saved as a version until you confirm. Pick what each column means, or ask Copilot below.</p>";
  (r.tables || []).forEach(function (t) {
    var k = t.kind;
    h += '<h4 style="margin:14px 0 4px">' + esc(KIND_TITLE[k]) + "</h4>";
    h += '<div class="row"><label>Sheet</label><select id="rv_sheet_' + k + '">' + p.sheets.map(function (s) { return "<option" + (s === t.sheet ? " selected" : "") + ">" + esc(s) + "</option>"; }).join("") +
      '</select><label>Header row</label><input type="number" min="1" id="rv_row_' + k + '" value="' + t.header_row + '" style="width:80px"><button class="alt" onclick="rereadTable(\'' + k + '\')">Re-read this row</button>' +
      (k === "rules" ? '<label><input type="checkbox" id="rv_skip_rules"> This file has no joins/filters table</label>' : "") + "</div>";
    h += table(["Column", "Header in the file", "Example values", "This column is", "Confidence"], t.columns.map(function (c) {
      var opts = '<option value="">(not used)</option>' + p.fields[k].map(function (f) { return '<option value="' + f.field + '"' + (f.field === c.field ? " selected" : "") + ">" + esc(f.label) + "</option>"; }).join("");
      return [esc(c.letter), esc(c.header || "(none)"), '<span class="hint">' + esc(c.sample.join(" | ")).slice(0, 120) + "</span>", '<select id="rv_col_' + k + "_" + c.letter + '">' + opts + "</select>", levelPill(c)];
    }));
    if (t.missing && t.missing.length && !t.usable) h += '<p class="hint">Still needed: ' + (k === "mapping" ? "a Target Column and a Source Column (or a transformation rule)" : "a join/filter expression") + ".</p>";
  });
  (r.warnings || []).forEach(function (w) { h += '<p class="hint">&bull; ' + esc(w) + "</p>"; });
  h += '<details><summary>Show the first rows of each sheet</summary>' + Object.keys(p.preview).map(function (n) {
    return "<h4 style=\"margin:10px 0 2px\">" + esc(n) + "</h4>" + table(["Row", "Cells"], p.preview[n].map(function (x) { return [x.row, '<span class="hint">' + esc(Object.keys(x.cells).map(function (c) { return c + ": " + x.cells[c]; }).join("  |  ")) + "</span>"]; }));
  }).join("") + "</details>";
  h += '<div class="row" style="margin-top:12px"><button class="alt" onclick="applyChoices()">Apply my choices</button><button id="rvSave" onclick="saveReading()"' + (p.ok ? "" : " disabled") +
    ">Save this reading as a new version</button><button class=\"alt\" onclick=\"discardUpload()\">Cancel upload</button></div>";
  return h;
}
function collectOverrides(p, only) {
  var out = {};
  ((p.report || {}).tables || []).forEach(function (t) {
    var k = t.kind;
    if (k === "rules" && $("rv_skip_rules") && $("rv_skip_rules").checked) { out[k] = { skip: true }; return; }
    var o = { sheet: $("rv_sheet_" + k).value, header_row: parseInt($("rv_row_" + k).value, 10) || t.header_row };
    if (only !== k) { o.columns = {}; t.columns.forEach(function (c) { var v = $("rv_col_" + k + "_" + c.letter).value; if (v) o.columns[c.letter] = v; }); }
    out[k] = o;
  });
  return out;
}

function testsHtml(g) {
  var t = g.tests, c = t && t.check, h = '<h3 style="font-size:15px;margin:18px 0 4px">Unit tests</h3>';
  if (!t || !t.code) return h + '<div class="empty">Copilot\'s reply had no "## Unit tests" section, so there are no tests for this code yet. Build the prompt again (it now asks for tests) and paste the new reply.</div>';
  if (!c.parses) return h + '<p class="hint"><b>' + esc(c.error) + "</b></p>";
  var cn = c.counts;
  h += tiles([[c.tests, "Test functions"], [cn.tested, "Items tested"], [cn.weak + cn.skipped, "Weak or skipped"], [cn.missing, "No test"]]);
  c.problems.forEach(function (p) { h += '<p class="hint">&bull; ' + esc(p) + "</p>"; });
  var open = c.rows.filter(function (r) { return r.status !== "tested"; });
  h += open.length ? '<p class="hint">Columns and rules that still need a proper test (' + open.length + "):</p>" + table(["Kind", "Item", "Status", "Note"], open.slice(0, 60).map(function (r) {
    return [esc(r.kind), esc(r.ref + " " + r.name), '<span class="pill ' + (r.status === "missing" ? "bad" : "warn") + '" style="margin-left:0">' + r.status + "</span>", esc(r.note)];
  })) : '<p class="hint">Every mapped column and rule has a test that asserts something. The tool checks that tests exist and assert; a person must still check the expected values are right.</p>';
  return h + "<details><summary>Show the test file</summary><div class=\"codebox\">" + esc(t.code) + "</div></details>";
}
