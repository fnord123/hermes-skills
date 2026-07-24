#!/usr/bin/env python3
"""Tests for browse_task.py.

Runs the wrapper as a subprocess against a FAKE `fara-cli` (a small script
written to a temp FARA_HOME) so the wrapper's logic — config loading, the
read-only directive, /dev/null stdin, trajectory parsing, and the
status→output mapping — is verified without the real Fara scaffold or model.

Run:  python3 browse_task_test.py
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HAS_XVFB = shutil.which("xvfb-run") is not None

SCRIPT = str(Path(__file__).resolve().parent / "browse_task.py")

# A fake fara-cli: records the task/stdin it received, then writes a
# data_point.json whose status/answer are driven by env vars the test sets.
FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
def val(flag):
    return args[args.index(flag) + 1] if flag in args else None
task = val("--task") or ""
out_folder = val("--output_folder") or "."
stdin_data = sys.stdin.read()   # should be empty (/dev/null)
rec = {"task": task, "stdin_empty": stdin_data == "", "model": val("--model"),
       "base_url": val("--base_url"), "max_rounds": val("--max_rounds")}
Path(os.environ["FAKE_CLI_RECORD"]).write_text(json.dumps(rec))
status = os.environ.get("FAKE_STATUS", "complete")
answer = os.environ.get("FAKE_ANSWER", "the answer")
d = Path(out_folder) / "run-1"
d.mkdir(parents=True, exist_ok=True)
(d / "data_point.json").write_text(json.dumps(
    {"status": status, "outcome": {"answer": answer},
     "actions": [1, 2, 3]}))
print("Final Answer:", answer)
'''


class BrowseTaskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="browse_task_test_"))
        cls.fara_home = cls.tmp / "fara"
        binp = cls.fara_home / ".venv" / "bin"
        binp.mkdir(parents=True)
        cli = binp / "fara-cli"
        cli.write_text(FAKE_CLI)
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
        cls.record = cls.tmp / "record.json"
        cls.config = cls.tmp / "config.env"
        cls.config.write_text(
            f"FARA_HOME={cls.fara_home}\n"
            "BROWSE_BASE_URL=http://fake:4000/v1\n"
            "BROWSE_MODEL=fara\n"
            "BROWSE_API_KEY=k\n")
        cls.log = cls.tmp / "browse.log"
        cls.base_env = {**os.environ,
                        "BROWSE_TASK_CONFIG": str(cls.config),
                        "BROWSE_TASK_LOG": str(cls.log),
                        "BROWSE_HEADFUL": "false",   # run the fake CLI directly, no xvfb
                        "FAKE_CLI_RECORD": str(cls.record)}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cmd(self, *args, **env):
        e = {**self.base_env, **env}
        p = subprocess.run([sys.executable, SCRIPT, *args], env=e,
                           capture_output=True, text=True, timeout=60)
        lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
        return p.returncode, (json.loads(lines[-1]) if lines else None)

    # ── happy path ───────────────────────────────────────────────────────────
    def test_readonly_complete(self):
        rc, d = self.run_cmd("--task", "find the price", FAKE_STATUS="complete",
                             FAKE_ANSWER="$42")
        self.assertEqual(rc, 0)
        self.assertTrue(d["ok"])
        self.assertEqual(d["status"], "complete")
        self.assertEqual(d["answer"], "$42")
        self.assertFalse(d["acted"])
        self.assertEqual(d["steps"], 3)

    def test_readonly_directive_and_stdin(self):
        self.run_cmd("--task", "find the price", FAKE_STATUS="complete")
        rec = json.loads(self.__class__.record.read_text())
        self.assertIn("read-only lookup", rec["task"])        # read-only appended
        self.assertTrue(rec["stdin_empty"])                  # /dev/null stdin
        self.assertEqual(rec["model"], "fara")               # config threaded through

    def test_confirm_drops_readonly_directive(self):
        rc, d = self.run_cmd("--task", "book it", "--confirm", FAKE_STATUS="complete")
        self.assertTrue(d["acted"])
        rec = json.loads(self.__class__.record.read_text())
        self.assertNotIn("read-only lookup", rec["task"])
        self.assertEqual(rec["task"], "book it")

    def test_logs_command_and_full_output(self):
        self.run_cmd("--task", "find the price", FAKE_STATUS="complete", FAKE_ANSWER="$42")
        text = self.__class__.log.read_text()
        self.assertIn("START", text)
        self.assertIn("--api_key <redacted>", text)      # key never logged in clear
        self.assertIn("Final Answer: $42", text)          # FULL agent output captured
        self.assertIn("RESULT", text)

    @unittest.skipUnless(HAS_XVFB, "xvfb-run not available")
    def test_headful_mode_wraps_with_xvfb(self):
        rc, d = self.run_cmd("--task", "x", FAKE_STATUS="complete", BROWSE_HEADFUL="auto")
        self.assertEqual(rc, 0)
        text = self.__class__.log.read_text()
        self.assertIn("mode=headful-xvfb", text)
        self.assertIn("xvfb-run", text)
        self.assertIn("--headful", text)

    # ── other statuses ───────────────────────────────────────────────────────
    def test_waiting_for_user_is_needs_input(self):
        rc, d = self.run_cmd("--task", "order lunch", FAKE_STATUS="waiting_for_user",
                             FAKE_ANSWER="Which restaurant?")
        self.assertEqual(rc, 0)
        self.assertTrue(d["ok"])
        self.assertEqual(d["status"], "needs_input")
        self.assertEqual(d["question"], "Which restaurant?")

    def test_max_rounds_is_failure(self):
        rc, d = self.run_cmd("--task", "huge task", FAKE_STATUS="max_rounds",
                             FAKE_ANSWER="got halfway")
        self.assertEqual(rc, 1)
        self.assertFalse(d["ok"])
        self.assertEqual(d["status"], "max_rounds")
        self.assertIn("got halfway", d["error"])

    # ── config / setup errors ────────────────────────────────────────────────
    def test_not_configured(self):
        rc, d = self.run_cmd("--task", "x", BROWSE_TASK_CONFIG=str(self.tmp / "nope.env"))
        self.assertEqual(rc, 1)
        self.assertIn("not configured", d["error"])

    def test_cli_missing(self):
        cfg = self.tmp / "badhome.env"
        cfg.write_text("FARA_HOME=/no/such/place\nBROWSE_BASE_URL=http://x/v1\nBROWSE_MODEL=fara\n")
        rc, d = self.run_cmd("--task", "x", BROWSE_TASK_CONFIG=str(cfg))
        self.assertEqual(rc, 1)
        self.assertIn("not installed", d["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
