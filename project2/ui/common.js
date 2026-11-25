/* Shared by the Understand, Code->Mapping, Mapping->Code and Reconcile pages. Load md.js and nav.js first. */
var job = null, cfg = {};
var $ = function (id) { return document.getElementById(id); };
var esc = escapeHtml;

function toast(t, bad) {
  var m = $("msg"); m.textContent = t; m.className = bad ? "err" : "good"; m.style.display = "block";
  clearTimeout(m.t); m.t = setTimeout(function () { m.style.display = "none"; }, bad ? 10000 : 2500);
}
function busy(text) {
  var m = $("msg"); clearTimeout(m.t);
  if (text) { document.body.classList.add("busy"); m.className = "wait"; m.innerHTML = '<span class="spin"></span>' + esc(text); m.style.display = "block"; }
  else { document.body.classList.remove("busy"); if (m.className === "wait") m.style.display = "none"; }
}
async function call(method, url, body) {
  var r = await fetch(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  var t = await r.text(), d; try { d = JSON.parse(t); } catch (e) { d = t; }
  if (!r.ok) throw new Error(d.error || t);
  return d;
}
/* run fn with a "working" message; show errors as a toast */
async function work(text, fn) {
  busy(text);
  try { return await fn(); } catch (e) { busy(false); toast(e.message, true); } finally { busy(false); }
}
function b64(buf) {
  var s = "", a = new Uint8Array(buf);
  for (var i = 0; i < a.length; i += 0x8000) s += String.fromCharCode.apply(null, a.subarray(i, i + 0x8000));
  return btoa(s);
}

/* ---------- job picker ---------- */
async function startPage(onLoad) {
  cfg = await call("GET", "/api/settings");
  $("jobbar").innerHTML = '<b>Migration</b><select id="job"></select><input id="newName" placeholder="new migration name" size="22"><button class="red" id="newJobBtn">+ New migration</button>' +
    '<span class="hint">A migration keeps the files, replies, versions and sign-offs for one modernization.</span>';
  var refresh = async function (sel) {
    var d = await call("GET", "/api/jobs"), s = $("job");
    s.innerHTML = d.jobs.map(function (j) { return "<option>" + esc(j) + "</option>"; }).join("");
    var want = sel || new URLSearchParams(location.search).get("job") || localStorage.getItem("pw_job");
    if (want && d.jobs.indexOf(want) >= 0) s.value = want;
    s.onchange = function () { select(s.value); };
    $("content").hidden = !d.jobs.length;
    $("nojob").hidden = d.jobs.length > 0;
    if (d.jobs.length) select(s.value);
  };
  var select = function (name) { job = name; localStorage.setItem("pw_job", name); onLoad(name); };
  $("newJobBtn").onclick = async function () {
    try { var n = $("newName").value.trim(); await call("POST", "/api/jobs", { name: n }); $("newName").value = ""; await refresh(n); toast("Migration created"); }
    catch (e) { toast(e.message, true); }
  };
  await refresh();
}

/* ---------- script upload card ---------- */
function mountSources(id, onChange) {
  $(id).innerHTML = '<div class="row"><input type="file" id="srcFile" multiple> <span class="hint">SQL, Python, SAS, COBOL, shell or any text script</span></div>' +
    '<details><summary>or paste it</summary><input type="text" id="pName" placeholder="File name, e.g. my_script.sql"><textarea id="pSrc" placeholder="Paste the script here"></textarea>' +
    '<button class="alt" id="pBtn">Save script</button></details><div id="srcList" class="chiplist" style="margin-top:8px"></div>';
  $("srcFile").onchange = async function () {
    var files = $("srcFile").files; if (!files.length) return;
    await work("Saving...", async function () {
      for (var i = 0; i < files.length; i++) await call("POST", "/api/job/" + job + "/file", { kind: "source", filename: files[i].name.replace(/[^A-Za-z0-9_.-]/g, "_"), content: await files[i].text() });
      $("srcFile").value = ""; toast("Script saved"); onChange();
    });
  };
  $("pBtn").onclick = async function () {
    var n = $("pName").value.trim(), t = $("pSrc").value;
    if (!n || !t.trim()) return toast("Enter a file name and paste the script.", true);
    await work("Saving...", async function () {
      await call("POST", "/api/job/" + job + "/file", { kind: "source", filename: n.replace(/[^A-Za-z0-9_.-]/g, "_"), content: t });
      $("pSrc").value = ""; $("pName").value = ""; toast("Script saved"); onChange();
    });
  };
}
function showSources(names) { $("srcList").innerHTML = names.length ? names.map(function (n) { return "<span>" + esc(n) + "</span>"; }).join("") : '<span class="hint">No script attached yet.</span>'; }

/* ---------- Copilot panel: build prompt, copy, paste reply, save, read nicely ---------- */
function mountPanel(id, step, title, extra, open) {
  $(id).innerHTML = "<details" + (open ? " open" : "") + "><summary>" + title + ' <span class="pill" id="saved_' + step + '" hidden>reply saved</span></summary>' +
    '<div class="hint">1) Build the prompt, 2) copy it into Copilot Chat, 3) paste the reply here and save.</div>' +
    '<div class="row"><button class="alt" onclick="buildPrompt(\'' + step + '\')">Build prompt</button><button class="alt" onclick="copyPrompt(\'' + step + '\')">Copy</button>' +
    '<button class="alt" id="ask_' + step + '" onclick="askCopilot(\'' + step + '\')" ' + (cfg.copilot_command ? "" : "hidden") + ">Ask Copilot directly</button></div>" +
    '<textarea id="p_' + step + '" readonly placeholder="Prompt appears here"></textarea><textarea id="a_' + step + '" placeholder="Paste Copilot\'s reply here"></textarea>' +
    '<div class="readbox md" id="r_' + step + '" hidden></div><div class="row"><button onclick="saveAnswer(\'' + step + '\')">Save reply</button>' +
    '<button class="alt" id="rb_' + step + '" onclick="toggleRead(\'' + step + '\')">Read nicely</button>' + (extra || "") + "</div></details>";
}
function fillPanel(step, text) {
  var a = $("a_" + step); if (!a) return;
  a.value = text || ""; $("saved_" + step).hidden = !(text || "").trim();
  if (!$("r_" + step).hidden) $("r_" + step).innerHTML = md(a.value);
}
async function buildPrompt(step) {
  var q = window.promptQuery ? window.promptQuery(step) : "";
  try { var d = await call("GET", "/api/job/" + job + "/prompt?step=" + step + q); $("p_" + step).value = d.prompt; toast("Prompt ready. Click Copy."); } catch (e) { toast(e.message, true); }
}
async function copyPrompt(step) {
  var t = $("p_" + step); if (!t.value) return toast("Click Build prompt first.", true);
  t.select(); try { await navigator.clipboard.writeText(t.value); } catch (e) { document.execCommand("copy"); }
  toast("Copied. Paste it into Copilot Chat.");
}
async function saveAnswer(step, quiet) {
  try { await call("POST", "/api/job/" + job + "/file", { kind: "answer", filename: step, content: $("a_" + step).value }); $("saved_" + step).hidden = !$("a_" + step).value.trim(); if (!quiet) toast("Reply saved"); return true; }
  catch (e) { toast(e.message, true); return false; }
}
async function askCopilot(step) {
  await work("Asking Copilot... please wait", async function () { var d = await call("POST", "/api/job/" + job + "/ask_copilot", { step: step }); $("a_" + step).value = d.answer; $("saved_" + step).hidden = false; toast("Reply saved"); });
}
function toggleRead(step) {
  var r = $("r_" + step), a = $("a_" + step);
  if (r.hidden) { r.innerHTML = a.value.trim() ? md(a.value) : '<p class="hint">Nothing saved yet.</p>'; r.hidden = false; a.hidden = true; $("rb_" + step).textContent = "Edit reply"; }
  else { r.hidden = true; a.hidden = false; $("rb_" + step).textContent = "Read nicely"; }
}
async function approveItem(artifact, nameId, commentId) {
  await work("Saving...", async function () {
    await call("POST", "/api/job/" + job + "/approve", { artifact: artifact, decision: "approve", reviewer: $(nameId).value, comment: commentId ? $(commentId).value : "" });
    toast("Sign-off recorded"); await reload();
  });
}
function reportLinks(id) { $(id).innerHTML = '<a class="btn alt" target="_blank" rel="noopener" href="/report?job=' + encodeURIComponent(job) + '">Open evidence report in new tab &#8599;</a>'; }
