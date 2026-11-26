"""The web page server.   Run:  python app.py     then open http://127.0.0.1:8000

Only listens on this computer (HOST in .env). Standard library only.
"""
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import core
import workflows

CFG = core.Settings()
MAX_BODY = 20 * 1024 * 1024
# address -> file in ui/
PAGES = {"/": "home.html", "/parity": "index.html", "/understand": "understand.html", "/mapping-to-code": "mapping-to-code.html",
         "/code-to-mapping": "code-to-mapping.html", "/reconcile": "reconcile.html", "/report": "report.html"}


def api(method, path, query, body):
    """Return (status, payload). payload is a dict, or a (content_type, text) tuple."""
    parts = [p for p in path.split("/") if p][1:]          # drop leading 'api'
    if method == "GET" and parts == ["settings"]:
        return {"reviewer": CFG.reviewer, "target": CFG.target, "copilot_command": bool(CFG.copilot_cmd), "dialect": CFG.dialect}
    if method == "GET" and parts == ["jobs"]:
        return {"jobs": core.list_jobs(CFG)}
    if method == "POST" and parts == ["reconcile"]:
        return core.reconcile(CFG, body.get("expected", ""), body.get("actual", ""), body.get("keys", ""), body.get("tolerance", "0"))
    if method == "POST" and parts == ["jobs"]:
        core.create_job(CFG, body.get("name", ""))
        return {"ok": True}
    if len(parts) >= 2 and parts[0] == "job":
        name, action = parts[1], (parts[2] if len(parts) > 2 else "")
        if method == "GET" and action == "":
            return core.job_state(CFG, name)
        if method == "GET" and action == "prompt":
            return {"prompt": core.build_prompt(CFG, name, query.get("step", ""), query)}
        if method == "GET" and action == "scan":
            return workflows.scan_job(CFG, name, log=query.get("quiet") != "1")
        if method == "GET" and action == "mapping":
            return workflows.mapping_state(CFG, name)
        if method == "GET" and action == "derived":
            return workflows.derived_state(CFG, name)
        if method == "GET" and action == "doc":
            return {"markdown": workflows.documentation(CFG, name, query.get("version"), query.get("target"))}
        if method == "GET" and action == "download":
            return ("bytes",) + workflows.download(CFG, name, query.get("what", ""), query.get("version"))
        if method == "GET" and action == "derived_compare":
            return workflows.derived_compare(CFG, name, query.get("from"), query.get("to"))
        if method == "GET" and action == "mapping_compare":
            return workflows.compare_versions(CFG, name, query.get("from"), query.get("to"))
        if method == "GET" and action == "audit":
            return {"audit": core.read_audit(CFG, name)}
        if method == "GET" and action == "report_data":
            return core.report_data(CFG, name)
        if method == "GET" and action == "report":
            return ("text/markdown; charset=utf-8", core.build_report(CFG, name))
        if method == "POST":
            if action == "file":
                core.write_file(CFG, name, body.get("kind", ""), body.get("content", ""), body.get("filename"))
                return {"ok": True}
            if action == "meta":
                path = core.job_dir(CFG, name)
                meta = core.load_meta(path)
                for key in ("key_columns", "tolerance", "legacy_cmd", "modern_cmd"):
                    if key in body:
                        meta[key] = str(body[key])
                core.save_meta(path, meta)
                return {"ok": True}
            if action == "mapping_upload":
                return workflows.save_mapping_upload(CFG, name, body.get("filename", ""), body.get("b64", ""), body.get("note", ""))
            if action == "mapping_resolve":
                return workflows.resolve_pending(CFG, name, body.get("overrides"), bool(body.get("use_reply")))
            if action == "mapping_confirm":
                return workflows.confirm_pending(CFG, name)
            if action == "mapping_discard":
                return workflows.discard_pending(CFG, name)
            if action == "save_generated":
                return workflows.save_generated(CFG, name, body.get("target", ""))
            if action == "parse_reply":
                return workflows.parse_reply(CFG, name, body.get("note", ""))
            if action == "use_generated_input":
                path = core.job_dir(CFG, name)
                answer = core.read_text(path / "answers" / "gen_input.md")
                if not answer.strip():
                    raise core.UserError("Save Copilot's sample-data answer first.")
                lines = [l for l in answer.strip().splitlines() if not l.strip().startswith("```")]
                core.write_file(CFG, name, "input", "\n".join(lines) + "\n")
                meta = core.load_meta(path)
                meta["input_origin"] = "generated"
                core.save_meta(path, meta)
                return {"ok": True}
            if action == "use_generated_expected":
                path = core.job_dir(CFG, name)
                answer = core.read_text(path / "answers" / "gen_expected.md")
                if not answer.strip():
                    raise core.UserError("Save Copilot's reply first.")
                lines = [l for l in answer.strip().splitlines() if not l.strip().startswith("```")]
                core.write_file(CFG, name, "expected", "\n".join(lines) + "\n")
                meta = core.load_meta(path)
                meta["expected_origin"] = core.UNVERIFIED
                core.save_meta(path, meta)
                return {"ok": True}
            if action == "ask_copilot":
                return {"answer": core.ask_copilot(CFG, name, body.get("step", ""))}
            if action == "run_legacy":
                return {"message": core.run_legacy(CFG, name)}
            if action == "run_modern":
                return {"message": core.run_modern(CFG, name)}
            if action == "check":
                return core.run_check(CFG, name)
            if action == "approve":
                core.approve(CFG, name, body.get("artifact", ""), body.get("decision", ""),
                             body.get("reviewer", ""), body.get("comment", ""))
                return {"ok": True}
    raise core.UserError("Unknown request.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):          # keep the console quiet
        pass

    def send(self, status, content_type, text):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_static(self, name):
        """Serve one file from ui/ (css, js, svg, png). Names with slashes are refused."""
        kinds = {"css": "text/css; charset=utf-8", "js": "application/javascript; charset=utf-8",
                 "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
        target = core.ROOT / "ui" / name
        kind = kinds.get(name.rsplit(".", 1)[-1])
        if not (core.FILE_RE.match(name) and kind and target.is_file()):
            return self.send(404, "text/plain", "not found")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self, method):
        url = urlparse(self.path)
        if method == "GET" and url.path in PAGES:
            return self.send(200, "text/html; charset=utf-8", (core.ROOT / "ui" / PAGES[url.path]).read_text(encoding="utf-8"))
        if method == "GET" and url.path.startswith("/ui/"):
            return self.send_static(url.path[len("/ui/"):])
        if not url.path.startswith("/api/"):
            return self.send(404, "text/plain", "not found")
        # Block other websites from calling this local API from a browser.
        if method == "POST" and self.headers.get("Origin") not in (None, "http://%s" % self.headers.get("Host")):
            return self.send(403, "application/json", json.dumps({"error": "Blocked: request came from another site."}))
        try:
            body = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise core.UserError("File too large for the page (limit 20 MB).")
                body = json.loads(self.rfile.read(length) or b"{}")
            result = api(method, url.path, {k: v[0] for k, v in parse_qs(url.query).items()}, body)
            if isinstance(result, tuple) and result[0] == "bytes":
                _, filename, ctype, data = result
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
                self.end_headers()
                return self.wfile.write(data)
            if isinstance(result, tuple):
                return self.send(200, result[0], result[1])
            self.send(200, "application/json", json.dumps(result))
        except core.UserError as error:
            self.send(400, "application/json", json.dumps({"error": str(error)}))
        except Exception as error:                      # unexpected: show it instead of hanging
            self.send(500, "application/json", json.dumps({"error": "Unexpected error: %r" % error}))

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


def main():
    CFG.jobs_dir.mkdir(parents=True, exist_ok=True)
    server = HTTPServer((CFG.host, CFG.port), Handler)
    url = "http://%s:%d" % (CFG.host, CFG.port)
    print("BMO ParityPilot running at %s  (Ctrl+C to stop)" % url)
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
