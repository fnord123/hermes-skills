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
       "base_url": val("--base_url"), "max_rounds": val("--max_rounds"),
       "init_cookies": os.environ.get("FARA_INIT_COOKIES"),
       "browserbase": "--browserbase" in args, "headful": "--headful" in args}
Path(os.environ["FAKE_CLI_RECORD"]).write_text(json.dumps(rec))
status = os.environ.get("FAKE_STATUS", "complete")
answer = os.environ.get("FAKE_ANSWER", "the answer")
d = Path(out_folder) / "run-1"
(d / "solver_log").mkdir(parents=True, exist_ok=True)
(d / "data_point.json").write_text(json.dumps(
    {"solver_log": {"status": status, "outcome": {"answer": answer}}}))
(d / "solver_log" / "events.jsonl").write_text(
    '{"type": "action"}\n{"type": "action"}\n{"type": "action"}\n')
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
        (binp / "python").write_text("#!/bin/sh\n")   # stub; probe uses BROWSE_PROBE_MAP hook
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
                        "BROWSE_HERMES_ENV": str(cls.tmp / "no-hermes-env"),  # isolate
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
        self.assertIn("browser mode=headful", text)
        self.assertIn("xvfb-run", text)
        self.assertIn("--headful", text)

    # ── per-site policy ──────────────────────────────────────────────────────
    def test_policy_reddit_headless(self):
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.reddit.com/r/x",
                             BROWSE_HEADFUL="", FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        text = self.__class__.log.read_text()
        self.assertIn("browser mode=headless (policy:reddit.com)", text)
        rec = json.loads(self.__class__.record.read_text())
        self.assertFalse(rec["headful"])

    @unittest.skipUnless(HAS_XVFB, "xvfb-run not available")
    def test_policy_amazon_headful(self):
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.amazon.com/s?k=lg",
                             BROWSE_HEADFUL="", FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        self.assertIn("browser mode=headful (policy:amazon.)",
                      self.__class__.log.read_text())

    def test_policy_costco_browserbase_needs_creds(self):
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.costco.com/",
                             BROWSE_HEADFUL="")
        self.assertEqual(rc, 1)
        self.assertIn("BrowserBase", d["error"])

    def test_browserbase_with_creds_runs(self):
        cfg = self.tmp / "bb.env"
        cfg.write_text(f"FARA_HOME={self.fara_home}\nBROWSE_BASE_URL=http://x/v1\n"
                       "BROWSE_MODEL=fara\nBROWSERBASE_API_KEY=bk\nBROWSERBASE_PROJECT_ID=bp\n")
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.costco.com/",
                             BROWSE_TASK_CONFIG=str(cfg), BROWSE_HEADFUL="",
                             FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        rec = json.loads(self.__class__.record.read_text())
        self.assertTrue(rec["browserbase"])

    # ── browserbase can be switched off independently ────────────────────────
    # It is the only rung that leaves the machine and bills a metered account, so it has to be
    # disableable without giving up the free local modes — both to test the cheaper layers
    # honestly and to stop an unwatched escalation spending money.
    def _log_tail(self, before):
        """Only what THIS run appended. The log is shared by every test in the class, so a
        bare assertNotIn trips over an earlier test that legitimately used browserbase."""
        return self.__class__.log.read_text()[before:]

    def test_no_browserbase_flag_demotes_to_local(self):
        before = len(self.__class__.log.read_text())
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.costco.com/",
                             "--no-browserbase", BROWSE_HEADFUL="", FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        tail = self._log_tail(before)
        self.assertIn("no-browserbase", tail)
        self.assertNotIn("browser mode=browserbase", tail)

    def test_no_browserbase_via_config(self):
        cfg = self.tmp / "nobb.env"
        cfg.write_text(f"FARA_HOME={self.fara_home}\nBROWSE_BASE_URL=http://x/v1\n"
                       "BROWSE_MODEL=fara\nBROWSE_NO_BROWSERBASE=true\n"
                       "BROWSERBASE_API_KEY=bk\nBROWSERBASE_PROJECT_ID=bp\n")
        before = len(self.__class__.log.read_text())
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.costco.com/",
                             BROWSE_TASK_CONFIG=str(cfg), BROWSE_HEADFUL="",
                             FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        self.assertNotIn("browser mode=browserbase", self._log_tail(before))

    def test_no_browserbase_leaves_local_policy_alone(self):
        before = len(self.__class__.log.read_text())
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.amazon.com/s?k=lg",
                             "--no-browserbase", BROWSE_HEADFUL="", FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        tail = self._log_tail(before)
        self.assertIn("browser mode=headful (policy:amazon.)", tail)
        self.assertNotIn("no-browserbase", tail)

    # ── the dump ladder climbs, and prior experience only skips rungs below ──
    def test_dump_ladder_climbs_and_experience_skips_below(self):
        import importlib.util, types
        spec = importlib.util.spec_from_file_location("bt_mod", SCRIPT)
        bt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bt)

        def A(mode=None, no_browserbase=False, all_layers=False):
            return types.SimpleNamespace(mode=mode, no_browserbase=no_browserbase,
                                         all_layers=all_layers)

        xv = "/usr/bin/xvfb-run"
        bb = {"BROWSERBASE_API_KEY": "k", "BROWSERBASE_PROJECT_ID": "p",
              "BROWSE_HERMES_ENV": str(self.tmp / "no-hermes-env")}
        off = dict(bb, BROWSE_NO_BROWSERBASE="true")

        # an unknown site climbs every rung, cheapest first
        self.assertEqual(bt.dump_ladder_modes(A(), bb, "https://new.example/", xv)[0],
                         ["headless", "headful", "browserbase"])
        # experience skips the rungs BELOW it and keeps the dearer ones in reserve
        self.assertEqual(bt.dump_ladder_modes(A(), bb, "https://www.amazon.com/x", xv)[0],
                         ["headful", "browserbase"])
        # --all-layers throws that away: a learned entry can record a bug as site behaviour
        self.assertEqual(
            bt.dump_ladder_modes(A(all_layers=True), bb, "https://www.amazon.com/x", xv)[0],
            ["headless", "headful", "browserbase"])
        # the paid rung never appears when it is switched off
        self.assertNotIn("browserbase",
                         bt.dump_ladder_modes(A(), off, "https://new.example/", xv)[0])
        # an explicit --mode pins one rung and disables the ladder
        self.assertEqual(bt.dump_ladder_modes(A(mode="headless"), bb,
                                              "https://www.costco.com/x", xv)[0], ["headless"])

    def test_mode_override_beats_policy(self):
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.amazon.com/s?k=lg",
                             "--mode", "headless", BROWSE_HEADFUL="", FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        self.assertIn("browser mode=headless (override)", self.__class__.log.read_text())

    # ── auto-probe for unknown sites ─────────────────────────────────────────
    def test_autoprobe_learns_unknown_site(self):
        learned = self.tmp / "learned1.json"
        # unknown site; probe says headless loads it
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.example-shop.test/p",
                             BROWSE_HEADFUL="", BROWSE_PROBE_MAP='{"headless":"OK"}',
                             BROWSE_LEARNED_POLICY=str(learned), FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        self.assertIn("browser mode=headless (probed)", self.__class__.log.read_text())
        self.assertIn("example-shop.test", learned.read_text())
        # a second run reuses the learned cache (no probe needed)
        rc2, d2 = self.run_cmd("--task", "y", "--start-url", "https://www.example-shop.test/x",
                               BROWSE_HEADFUL="", BROWSE_LEARNED_POLICY=str(learned),
                               FAKE_STATUS="complete")
        self.assertEqual(rc2, 0)
        self.assertIn("browser mode=headless (learned)", self.__class__.log.read_text())

    def test_autoprobe_escalates_to_browserbase(self):
        learned = self.tmp / "learned2.json"
        cfg = self.tmp / "bb2.env"
        cfg.write_text(f"FARA_HOME={self.fara_home}\nBROWSE_BASE_URL=http://x/v1\n"
                       "BROWSE_MODEL=fara\nBROWSERBASE_API_KEY=bk\nBROWSERBASE_PROJECT_ID=bp\n")
        rc, d = self.run_cmd("--task", "x", "--start-url", "https://www.hardsite.test/",
                             BROWSE_TASK_CONFIG=str(cfg), BROWSE_HEADFUL="",
                             BROWSE_PROBE_MAP='{"headless":"BLOCKED","headful":"BLOCKED"}',
                             BROWSE_LEARNED_POLICY=str(learned), FAKE_STATUS="complete")
        self.assertEqual(rc, 0)
        self.assertIn("browser mode=browserbase (probed)", self.__class__.log.read_text())
        rec = json.loads(self.__class__.record.read_text())
        self.assertTrue(rec["browserbase"])

    def test_cookies_passed_through(self):
        cfile = self.tmp / "cookies.json"
        cfile.write_text('[{"name":"z","value":"97219","domain":".x.com","path":"/"}]')
        self.run_cmd("--task", "check stock", "--cookies", str(cfile), FAKE_STATUS="complete")
        rec = json.loads(self.__class__.record.read_text())
        self.assertEqual(rec["init_cookies"], str(cfile))   # FARA_INIT_COOKIES set for agent

    def test_missing_cookies_file_ignored(self):
        rc, d = self.run_cmd("--task", "x", "--cookies", str(self.tmp / "nope.json"),
                             FAKE_STATUS="complete")
        self.assertEqual(rc, 0)                              # not fatal
        rec = json.loads(self.__class__.record.read_text())
        self.assertIsNone(rec["init_cookies"])              # not passed when absent

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
