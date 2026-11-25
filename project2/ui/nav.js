/* Shared by every page: top navigation, logo, and the PASS/FAIL result view. Needs md.js first. */
function mainNav(active) {
  var links = [["/", "Home"], ["/parity", "Modernization Studio"], ["/understand", "Code Insights"], ["/code-to-mapping", "Mapping from Code"], ["/mapping-to-code", "Code from Mapping"], ["/reconcile", "Reconcile"]];
  var nav = document.createElement("nav");
  nav.className = "mainnav noprint";
  nav.innerHTML = links.map(function (l) { return '<a href="' + l[0] + '"' + (l[0] === active ? ' class="on"' : "") + ">" + l[1] + "</a>"; }).join("");
  var top = document.querySelector(".top");
  top.insertAdjacentElement("afterend", nav);
}

function loadLogo() {
  var img = document.getElementById("logo"), tries = ["/ui/logo.png", "/ui/logo.svg", "/ui/logo.jpg"];
  if (!img) return;
  function next() { if (tries.length) img.src = tries.shift(); }
  img.onload = function () { img.style.display = "block"; };
  img.onerror = next;
  next();
}

/* r = result from the compare. Returns {cls, verdict, details} as HTML strings. */
function resultView(r, label) {
  var e = escapeHtml;
  var cls = r.unverified ? "warn" : (r.passed ? "pass" : "fail");
  var verdict = e(label) + ": " + (r.passed ? "PASS" : "FAIL") + "<small>" + e(r.data_label) + " &middot; " + r.diff_count + " difference(s) &middot; rows: " + r.expected_rows + " vs " +
    r.actual_rows + " &middot; compared " + (r.key_columns.length ? "by " + e(r.key_columns.join(",")) : "row by row") + "</small>";
  var h = "";
  if (r.unverified) h += '<details class="callout small"><summary>&#9888; Unverified: legacy output was predicted by Copilot</summary>The legacy output was predicted by Copilot, not produced by the real legacy system. A PASS here is not proof. Get the real output and re-check.</details>';
  else if (r.passed && r.data_label === "sample data") h += '<details class="callout small"><summary>&#9888; Passed on generated sample data only</summary>Real data may expose cases the sample missed.</details>';
  var cd = Object.keys(r.columns_diverged || {});
  if (cd.length) h += "<p>Columns that differ: " + cd.map(function (c) { return e(c) + " (" + r.columns_diverged[c] + ")"; }).join(", ") + "</p>";
  if (r.diffs.length) h += '<div class="tscroll"><table><tr><th>Type</th><th>Where</th><th>Column</th><th>Left / legacy</th><th>Right / new</th></tr>' +
    r.diffs.map(function (x) { return "<tr><td>" + e(x.type) + "</td><td>" + e(x.where) + "</td><td>" + e(x.column) + "</td><td>" + e(x.expected) + "</td><td>" + e(x.actual) + "</td></tr>"; }).join("") + "</table></div>";
  if (r.preview) {
    var pv = r.preview;
    var tbl = function (rows, other) {
      return '<div class="tscroll"><table><tr>' + pv.header.map(function (c) { return "<th>" + e(c) + "</th>"; }).join("") + "</tr>" +
        rows.map(function (row, i) { return "<tr>" + row.map(function (v, j) { var o = other[i] && other[i][j]; return "<td" + (o !== undefined && o !== v ? ' class="diffcell"' : "") + ">" + e(v) + "</td>"; }).join("") + "</tr>"; }).join("") + "</table></div>";
    };
    h += '<h3 style="font-size:14px;margin:16px 0 4px">First ' + Math.max(pv.expected.length, pv.actual.length) + ' rows of each file</h3><div class="two"><div><b>Left / legacy</b>' + tbl(pv.expected, pv.actual) +
      "</div><div><b>Right / new</b>" + tbl(pv.actual, pv.expected) + '</div></div><p class="hint">Red cells differ at the same row position. If you matched rows by key, row order may differ, so use the table above for real differences.</p>';
  }
  return { cls: cls, verdict: verdict, details: h };
}

/* ---------- small renderers ---------- */
function tiles(list) { return '<div class="tiles">' + list.map(function (t) { return '<div class="tile"><b>' + t[0] + "</b><span>" + escapeHtml(t[1]) + "</span></div>"; }).join("") + "</div>"; }
function sev(s) { return '<span class="sev ' + s + '">' + s.charAt(0).toUpperCase() + s.slice(1) + "</span>"; }
function table(head, rows) {
  return '<div class="tscroll"><table><tr>' + head.map(function (h) { return "<th>" + escapeHtml(h) + "</th>"; }).join("") + "</tr>" +
    rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>"; }).join("") + "</table></div>";
}
function chips(list) { return list.length ? '<div class="chiplist">' + list.map(function (x) { return "<span>" + escapeHtml(x) + "</span>"; }).join("") + "</div>" : '<span class="hint">none found</span>'; }
function lintHtml(list) {
  if (!list.length) return '<div class="empty">No problems found in the document.</div>';
  return table(["Severity", "Where", "What we found"], list.map(function (i) { return [sev(i.severity), escapeHtml(i.where), escapeHtml(i.message)]; }));
}
var STATUS_LABEL = { found: "Found", verify: "Verify", missing: "Missing", unchecked: "Not checked" };
function coverageHtml(cov, title) {
  var c = cov.counts, bad = cov.rows.filter(function (r) { return r.status === "missing" || r.status === "verify"; });
  var h = '<p><b>' + escapeHtml(title) + ":</b> " + c.found + " found, " + c.verify + " to verify, " + c.missing + " missing" + (c.unchecked ? ", " + c.unchecked + " not checked" : "") + " (of " + cov.total + " checks).</p>";
  if (cov.positional_insert) h += '<div class="callout small" style="padding:8px 12px;font-size:13px">&#9888; This INSERT has no column list, so target column names can not be checked. It relies on column order.</div>';
  h += bad.length ? table(["Status", "Item", "Name", "What to check"], bad.map(function (r) { return ['<span class="sev ' + r.status + '">' + STATUS_LABEL[r.status] + "</span>", escapeHtml(r.kind + " " + r.ref), escapeHtml(r.name), escapeHtml(r.note)]; })) :
    '<div class="empty">Everything the mapping asks for was found in the code.</div>';
  h += '<details><summary>Show all ' + cov.total + " checks</summary>" + table(["Status", "Item", "Name"], cov.rows.map(function (r) { return ['<span class="sev ' + r.status + '">' + STATUS_LABEL[r.status] + "</span>", escapeHtml(r.kind + " " + r.ref), escapeHtml(r.name)]; })) + "</details>";
  h += '<p class="hint">This is a text check: it looks for the names and expressions in the code. It does not run the code, so a person still has to confirm the logic.</p>';
  return h;
}
function nw(t) { return '<span style="white-space:nowrap">' + escapeHtml(t) + "</span>"; }
function mappingTable(model) {
  return table(["Map ID", "Source", "Target column", "Type", "Key", "Rule"], model.mappings.map(function (m) {
    var known = function (x) { return x && x !== "-" && x.toUpperCase() !== "UNKNOWN"; };
    var src = [m.source_schema, m.source_table, m.source_column].filter(known).join(".") || "-";
    return [escapeHtml(m.id), escapeHtml(src), "<b>" + escapeHtml(m.target_column) + "</b>", nw(m.target_type), nw(m.key), escapeHtml(m.logic)];
  }));
}
function rulesTable(model) {
  return table(["Seq", "Type", "Rule", "Join", "Expression", "Why"], model.rules.map(function (x) {
    return [escapeHtml(x.seq), escapeHtml(x.type), escapeHtml(x.name), escapeHtml((x.join_type && x.join_type !== "-" ? x.join_type : "") ), '<span class="mono" style="font-size:12px">' + escapeHtml(x.expr) + "</span>", escapeHtml(x.logic)];
  }));
}


/* ---------- mapping version changes ---------- */
function kindPill(k) {
  var m = { business: ["Logic change", "high"], technical: ["Technical", "medium"], notes: ["Notes only", "low"], order: ["Order", "info"] }[k] || [k, "low"];
  return '<span class="sev ' + m[1] + '">' + m[0] + "</span>";
}
function beforeAfter(a, b) { return ['<span class="chg-old">' + escapeHtml(a || "(blank)") + "</span>", '<span class="chg-new">' + escapeHtml(b || "(blank)") + "</span>"]; }
function diffHtml(d) {
  var c = d.counts, e = escapeHtml;
  if (!c.total) return '<div class="empty">' + e(d.summary) + "</div>";
  var h = '<div class="plain">' + e(d.summary) + "</div>" +
    tiles([[c.columns_changed, "Columns changed"], [c.columns_added, "Columns added"], [c.columns_removed, "Columns removed"], [c.rules_changed, "Rules changed"], [c.rules_added, "Rules added"], [c.rules_removed, "Rules removed"]]);
  var rows = [];
  d.columns.changed.forEach(function (x) {
    x.changes.forEach(function (ch) { var ba = beforeAfter(ch.old, ch.new); rows.push(["<b>" + e(x.target) + "</b>" + (x.renamed_from ? '<br><span class="hint">renamed from ' + e(x.renamed_from) + "</span>" : ""), kindPill(x.kind), e(ch.field), ba[0], ba[1]]); });
  });
  if (rows.length) h += '<h3 style="font-size:15px;margin:16px 0 4px">Columns changed</h3>' + table(["Column", "Kind", "Field", "Before", "After"], rows);
  var add = d.columns.added.map(function (m) { return ["<b>" + e(m.target_column) + "</b>", e(m.id), e([m.source_table, m.source_column].filter(Boolean).join(".")), e(m.logic)]; });
  if (add.length) h += '<h3 style="font-size:15px;margin:16px 0 4px">New columns</h3>' + table(["Column", "Map ID", "Source", "Rule"], add);
  var rem = d.columns.removed.map(function (m) { return ["<b>" + e(m.target_column) + "</b>", e(m.id), e(m.logic)]; });
  if (rem.length) h += '<h3 style="font-size:15px;margin:16px 0 4px">Removed columns</h3>' + table(["Column", "Map ID", "Old rule"], rem);
  var rr = [];
  d.rules.changed.forEach(function (x) {
    x.changes.forEach(function (ch) { var ba = beforeAfter(ch.old, ch.new); rr.push(["Rule " + e(x.seq) + "<br><b>" + e(x.name) + "</b>", kindPill(x.kind), e(ch.field), ba[0], ba[1], e((x.affects || []).join(", "))]); });
  });
  if (rr.length) h += '<h3 style="font-size:15px;margin:16px 0 4px">Rules changed</h3>' + table(["Rule", "Kind", "Field", "Before", "After", "Affects"], rr);
  var ra = d.rules.added.map(function (r) { return ["Rule " + e(r.seq), "<b>" + e(r.name) + "</b>", e(r.type), '<span class="mono" style="font-size:12px">' + e(r.expr) + "</span>", e((r.affects || []).join(", "))]; });
  if (ra.length) h += '<h3 style="font-size:15px;margin:16px 0 4px">New rules</h3>' + table(["Rule", "Name", "Type", "Expression", "Affects"], ra);
  var rd = d.rules.removed.map(function (r) { return ["Rule " + e(r.seq), "<b>" + e(r.name) + "</b>", e(r.type), '<span class="mono" style="font-size:12px">' + e(r.expr) + "</span>", e((r.affects || []).join(", "))]; });
  if (rd.length) h += '<h3 style="font-size:15px;margin:16px 0 4px">Removed rules</h3>' + table(["Rule", "Name", "Type", "Expression", "Was affecting"], rd);
  if (d.parameters.added.length || d.parameters.removed.length) h += '<p class="hint">Parameters: ' + d.parameters.added.map(function (p) { return "+:" + e(p); }).concat(d.parameters.removed.map(function (p) { return "-:" + e(p); })).join(", ") + "</p>";
  return h;
}
function codeDiffHtml(cd, touched, label) {
  var e = escapeHtml;
  if (cd.same) return '<div class="empty">The ' + e(label) + " code is identical in these two versions.</div>";
  var h = '<p class="hint">' + cd.added + " lines added, " + cd.removed + " lines removed" + (cd.truncated ? " (showing the first part)" : "") + '</p><div class="codebox">' +
    cd.lines.map(function (l) { return '<span class="dl dl-' + l.kind + '">' + e(l.text || " ") + "</span>"; }).join("") + "</div>";
  if (touched && touched.length) h += '<h4 style="margin:14px 0 4px;font-size:14px">Did the code change where the mapping changed?</h4>' +
    table(["Mapping change", "In the code diff"], touched.map(function (t) { return [e(t.item), t.status === "touched" ? '<span class="sev found">Code changed</span> <span class="hint">' + e(t.evidence.join(", ")) + "</span>" : '<span class="sev verify">No matching code change: check</span>']; })) +
    '<p class="hint">A text hint only: it looks for the changed names in the changed lines.</p>';
  return h;
}
