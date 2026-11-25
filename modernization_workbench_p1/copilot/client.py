"""Talks to GitHub Copilot - two ways, both logged.

1. COMMAND MODE: if COPILOT_CMD is set in .env, we run that command and save what it prints.
2. PASTE MODE:   if it is blank, we write the prompt to generated/prompts/<name>.md. You paste it
                 into Copilot Chat, save the reply as generated/copilot_answers/<name>.md, and run again.

Every prompt is written to audit/prompt_log.csv with the reviewer name (evidence of human review).
"""
import csv
import os
import shlex
import subprocess
from datetime import datetime

LOG_COLUMNS = ["time", "system", "step", "mode", "status", "prompt_file", "answer_file", "reviewer", "human_decision"]


def split_command(text):
    """Split a command line. Works on Windows too (keeps backslashes, drops outer quotes)."""
    parts = shlex.split(text, posix=(os.name != "nt"))
    return [p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def write_log(cfg, row):
    cfg.audit_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.audit_dir / "prompt_log.csv"
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(LOG_COLUMNS)
        writer.writerow([row.get(c, "") for c in LOG_COLUMNS])


def run_command(cfg, prompt, prompt_file):
    argv = split_command(cfg.copilot_cmd)
    uses_placeholder = any("{prompt}" in a or "{prompt_file}" in a for a in argv)
    argv = [a.replace("{prompt_file}", str(prompt_file)).replace("{prompt}", prompt) for a in argv]
    result = subprocess.run(argv, input=None if uses_placeholder else prompt, capture_output=True,
                            text=True, timeout=cfg.copilot_timeout)
    if result.returncode != 0:
        raise RuntimeError("Copilot command failed (%s): %s" % (result.returncode, result.stderr[-300:]))
    return result.stdout


def ask(cfg, step, prompt):
    """Send one prompt. Returns (status, answer_text_or_None)."""
    name = "%s_%s" % (cfg.system, step)
    prompt_file = cfg.generated_dir / "prompts" / (name + ".md")
    answer_file = cfg.generated_dir / "copilot_answers" / (name + ".md")
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    answer_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")

    answer, mode = None, "paste"
    if cfg.copilot_cmd:
        mode = "command"
        try:
            answer = run_command(cfg, prompt, prompt_file)
            answer_file.write_text(answer, encoding="utf-8")
            status = "ANSWER_SAVED"
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            status = "COMMAND_FAILED: %s" % str(error)[:120]
    elif answer_file.exists():
        answer, status = answer_file.read_text(encoding="utf-8"), "ANSWER_FOUND"
    else:
        status = "WAITING_FOR_PASTE"
        print("  [paste mode] paste %s into Copilot Chat, save the reply as %s" % (prompt_file, answer_file))

    write_log(cfg, {"time": datetime.now().isoformat(timespec="seconds"), "system": cfg.system, "step": step,
                    "mode": mode, "status": status, "prompt_file": str(prompt_file), "answer_file": str(answer_file),
                    "reviewer": cfg.reviewer, "human_decision": "PENDING"})
    return status, answer
