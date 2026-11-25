"""A pretend Copilot for offline demos and tests. Reads a prompt on stdin, prints a canned reply.

Use it with:  python modernize.py all --fake-copilot
Real Copilot output will of course be different and must be reviewed by a human.
"""
import sys

prompt = sys.stdin.read()
print("# SIMULATED COPILOT REPLY (demo only - not real Copilot output)")
print("")
print("Received a prompt of %d characters, %d lines." % (len(prompt), prompt.count("\n") + 1))
print("In a real run, Copilot's analysis, tests or PySpark code would appear here.")
print("A human reviewer must read it and record the decision in audit/prompt_log.csv.")
