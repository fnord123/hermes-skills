#!/usr/bin/env python3
"""Tests for wa_backfill.py.

Runs the skill as a subprocess against a KNOWN WhatsApp export zip, with the
real Hindsight client shadowed by an in-repo fake (an importable package written
to a temp dir and put on PYTHONPATH). The fake records what the skill submits
and serves file-backed state, so `preview`, `import --wait`, and `status` can be
exercised — and their JSON output asserted — without touching a real server.

Run:  python3 wa_backfill_test.py        (or: python3 -m unittest wa_backfill_test)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent / "wa_backfill.py")

# A known iOS-format export. Lines with no leading [timestamp] continue the
# previous message. Contains: a system line (no sender), a media-only line, and
# one multi-line message — so we can assert skipping and flattening.
CHAT_TXT = (
    "[2020-09-24, 2:00:00 PM] Messages to this chat are now secured with end-to-end encryption.\n"
    "[2020-09-24, 2:24:00 PM] Connie: Hype\n"
    "[2020-09-24, 2:43:00 PM] David: Test\n"
    "[2020-09-25, 11:41:00 AM] Connie: We can return the silicon bumpers.\n"
    "They dampen the slamming.\n"
    "[2020-09-26, 9:06:00 AM] Connie: The printer is jammed again\n"
    "[2020-09-26, 1:00:00 PM] Connie: image omitted\n"
    "[2020-09-26, 12:36:00 PM] David: Rick said hi\n"
)

# ── the fake hindsight_client_api package (file-backed, deterministic) ──────────
FAKE_PKG = {
    "hindsight_client_api/__init__.py": (
        "class Configuration:\n"
        "    def __init__(self, host=None, **kw): self.host = host\n"
        "class ApiClient:\n"
        "    def __init__(self, cfg=None): self.cfg = cfg\n"
        "    async def __aenter__(self): return self\n"
        "    async def __aexit__(self, *a): return False\n"
    ),
    "hindsight_client_api/_state.py": (
        "import json, os\n"
        "def _p(): return os.environ['WA_BACKFILL_FAKE_STATE']\n"
        "def load():\n"
        "    try:\n"
        "        with open(_p()) as f: return json.load(f)\n"
        "    except FileNotFoundError:\n"
        "        return {'banks': {}}\n"
        "def save(s):\n"
        "    with open(_p(), 'w') as f: json.dump(s, f)\n"
        "def bank(s, b): return s['banks'].setdefault(b, {'docs': [], 'ops': {}})\n"
    ),
    "hindsight_client_api/api/__init__.py": "",
    "hindsight_client_api/api/memory_api.py": (
        "from .._state import load, save, bank\n"
        "class _Resp:\n"
        "    def __init__(self, operation_id): self.operation_id = operation_id\n"
        "class MemoryApi:\n"
        "    def __init__(self, api=None): pass\n"
        "    async def retain_memories(self, bank_id, req, authorization=None):\n"
        "        s = load(); b = bank(s, bank_id)\n"
        "        items = list(getattr(req, 'items', None) or [])\n"
        "        idx = len(b['docs']) + 1\n"
        "        content = '\\n---\\n'.join(getattr(it, 'content', '') for it in items)\n"
        "        b['docs'].append({'id': 'doc-%d' % idx, 'memory_unit_count': len(items), 'content': content})\n"
        "        op = 'op-%d' % idx\n"
        "        b['ops'][op] = {'status': 'completed', 'items_count': len(items)}\n"
        "        save(s)\n"
        "        return _Resp(op)\n"
    ),
    "hindsight_client_api/api/operations_api.py": (
        "from .._state import load, bank\n"
        "class OperationsApi:\n"
        "    def __init__(self, api=None): pass\n"
        "    async def get_operation_status(self, bank_id, op, authorization=None):\n"
        "        s = load(); b = bank(s, bank_id); rec = b['ops'].get(op, {})\n"
        "        return {'status': rec.get('status', 'unknown'), 'operation_type': 'batch_retain',\n"
        "                'result_metadata': {'items_count': rec.get('items_count', 0)}}\n"
    ),
    "hindsight_client_api/api/documents_api.py": (
        "from .._state import load, bank\n"
        "class DocumentsApi:\n"
        "    def __init__(self, api=None): pass\n"
        "    async def list_documents(self, bank_id, authorization=None):\n"
        "        s = load(); b = bank(s, bank_id)\n"
        "        return {'items': [{'id': d['id']} for d in b['docs']], 'total': len(b['docs'])}\n"
        "    async def get_document(self, bank_id, doc_id, authorization=None):\n"
        "        s = load(); b = bank(s, bank_id)\n"
        "        for d in b['docs']:\n"
        "            if d['id'] == doc_id:\n"
        "                return {'memory_unit_count': d['memory_unit_count'], 'original_text': d['content']}\n"
        "        return {'memory_unit_count': 0}\n"
    ),
    "hindsight_client_api/api/banks_api.py": (
        "from .._state import load, save\n"
        "class BanksApi:\n"
        "    def __init__(self, api=None): pass\n"
        "    async def delete_bank(self, bank_id, authorization=None):\n"
        "        s = load(); b = s['banks'].get(bank_id, {'docs': [], 'ops': {}})\n"
        "        n = len(b.get('docs', [])) + len(b.get('ops', {}))\n"
        "        s['banks'].pop(bank_id, None); save(s)\n"
        "        return {'success': True, 'deleted_count': n, 'message': \"Bank '%s' deleted\" % bank_id}\n"
    ),
    "hindsight_client_api/models/__init__.py": "",
    "hindsight_client_api/models/retain_request.py": (
        "class RetainRequest:\n"
        "    def __init__(self, items=None, var_async=False, **kw):\n"
        "        self.items = items or []; self.var_async = var_async\n"
        "    @classmethod\n"
        "    def from_dict(cls, d):\n"
        "        r = cls(); r.items = d.get('items', []); return r\n"
    ),
    "hindsight_client_api/models/memory_item.py": (
        "class MemoryItem:\n"
        "    def __init__(self, content=None, timestamp=None, context=None, metadata=None, **kw):\n"
        "        self.content = content; self.timestamp = timestamp\n"
        "        self.context = context; self.metadata = metadata\n"
        "    def to_dict(self):\n"
        "        return {'content': self.content, 'context': self.context, 'metadata': self.metadata}\n"
    ),
    "hindsight_client_api/models/timestamp.py": (
        "class Timestamp:\n"
        "    def __init__(self, actual_instance=None, **kw): self.actual_instance = actual_instance\n"
    ),
}


class WaBackfillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wa_backfill_test_"))
        # fake hindsight_client_api package on PYTHONPATH (shadows the real one)
        cls.fake_root = cls.tmp / "fake"
        for rel, content in FAKE_PKG.items():
            f = cls.fake_root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        # throwaway Hindsight config (the fake ignores host/bank, just needs it valid)
        cls.config = cls.tmp / "hindsight.json"
        cls.config.write_text(json.dumps(
            {"api_url": "http://fake.invalid", "bank_id": "default", "apiKey": ""}))
        cls.state = cls.tmp / "state.json"
        # known export zip: name drives chat-label derivation -> "TestChat"
        cls.zip = cls.tmp / "WhatsApp_Chat__TestChat.zip"
        with zipfile.ZipFile(cls.zip, "w") as z:
            z.writestr("_chat.txt", CHAT_TXT)
        cls.env = {
            **os.environ,
            "PYTHONPATH": str(cls.fake_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "WA_BACKFILL_HINDSIGHT_CONFIG": str(cls.config),
            "WA_BACKFILL_FAKE_STATE": str(cls.state),
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cmd(self, *args):
        """Run the skill; return (returncode, parsed_json_stdout, stderr)."""
        p = subprocess.run([sys.executable, SCRIPT, *args], env=self.env,
                           capture_output=True, text=True, timeout=120)
        lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
        data = json.loads(lines[-1]) if lines else None
        return p.returncode, data, p.stderr

    # ── preview ────────────────────────────────────────────────────────────────
    def test_preview_parses_zip_and_stats(self):
        rc, d, _ = self.run_cmd("preview", "--file", str(self.zip), "--block-days", "7")
        self.assertEqual(rc, 0)
        self.assertTrue(d["ok"])
        self.assertEqual(d["chat"], "TestChat")            # derived from zip name
        self.assertEqual(d["messages_parsed"], 7)
        self.assertEqual(d["system_or_media_skipped"], 2)  # 1 system + 1 media
        self.assertEqual(d["blocks"], 1)                   # one 7-day window
        self.assertEqual(d["date_range"],
                         ["2020-09-24T14:00:00", "2020-09-26T13:00:00"])

    def test_preview_aggressive_formatting(self):
        rc, d, _ = self.run_cmd("preview", "--file", str(self.zip), "--block-days", "7")
        sample = d["sample_block"]
        self.assertIn("WhatsApp chat: TestChat", sample)
        self.assertIn("Participants: Connie, David", sample)
        self.assertIn("===== 2020-09-24 (Thursday) =====", sample)  # dated day header
        # multi-line message flattened to one line
        self.assertIn("[2020-09-25 11:41] Connie: We can return the silicon "
                      "bumpers. They dampen the slamming.", sample)

    def test_preview_alias_renames_sender(self):
        rc, d, _ = self.run_cmd("preview", "--file", str(self.zip),
                                "--block-days", "7", "--alias", "Connie=Constance")
        self.assertIn("Constance", d["sample_block"])
        self.assertNotIn("] Connie:", d["sample_block"])

    # ── import (mocked Hindsight) ────────────────────────────────────────────────
    def test_import_wait_reports_facts_landed(self):
        rc, d, err = self.run_cmd("import", "--file", str(self.zip),
                                  "--block-days", "7", "--bank", "imp1", "--wait")
        self.assertEqual(rc, 0)
        self.assertTrue(d["ok"])
        self.assertEqual(d["bank"], "imp1")
        self.assertEqual(d["blocks_submitted"], 1)
        self.assertEqual(d["batches"], 1)
        self.assertTrue(d["all_completed"])
        self.assertEqual(d["status_counts"], {"completed": 1})
        self.assertEqual(d["bank_summary"], {"documents": 1, "facts": 1})
        # progress went to stderr, not stdout
        self.assertIn("waiting for extraction", err)

    def test_import_submits_rendered_transcript(self):
        # verify the skill actually sent the date-emphasized transcript to Hindsight
        self.run_cmd("import", "--file", str(self.zip),
                     "--block-days", "7", "--bank", "imp2", "--wait")
        state = json.loads(self.state.read_text())
        content = state["banks"]["imp2"]["docs"][0]["content"]
        self.assertIn("===== 2020-09-26 (Saturday) =====", content)
        self.assertIn("[2020-09-26 12:36] David: Rick said hi", content)
        self.assertNotIn("image omitted", content)                 # media dropped
        self.assertNotIn("end-to-end encryption", content)         # system dropped

    # ── status (mocked Hindsight) ────────────────────────────────────────────────
    def test_status_reports_operation_and_counts(self):
        _, imp, _ = self.run_cmd("import", "--file", str(self.zip),
                                 "--block-days", "7", "--bank", "stat1")
        op = imp["operation_ids"][0]
        rc, d, _ = self.run_cmd("status", "--bank", "stat1", "--operation-id", op)
        self.assertEqual(rc, 0)
        self.assertTrue(d["ok"])
        self.assertEqual(d["operation_status"], {op: "completed"})
        self.assertTrue(d["all_completed"])
        self.assertEqual(d["bank_summary"], {"documents": 1, "facts": 1})

    # ── clear (mocked Hindsight) ─────────────────────────────────────────────────
    def test_clear_dry_run_keeps_data(self):
        self.run_cmd("import", "--file", str(self.zip),
                     "--block-days", "7", "--bank", "clr1", "--wait")
        rc, d, _ = self.run_cmd("clear", "--bank", "clr1")   # no --confirm
        self.assertEqual(rc, 0)
        self.assertFalse(d["deleted"])
        self.assertEqual(d["bank_summary"], {"documents": 1, "facts": 1})
        state = json.loads(self.state.read_text())
        self.assertIn("clr1", state["banks"])                # still there

    def test_clear_confirm_deletes(self):
        self.run_cmd("import", "--file", str(self.zip),
                     "--block-days", "7", "--bank", "clr2", "--wait")
        rc, d, _ = self.run_cmd("clear", "--bank", "clr2", "--confirm")
        self.assertEqual(rc, 0)
        self.assertTrue(d["deleted"])
        self.assertEqual(d["deleted_count"], 2)              # 1 doc + 1 op
        state = json.loads(self.state.read_text())
        self.assertNotIn("clr2", state["banks"])             # gone

    def test_clear_requires_bank(self):
        rc, d, err = self.run_cmd("clear", "--confirm")      # missing --bank
        self.assertEqual(rc, 2)                              # argparse usage error
        self.assertIn("--bank", err)

    # ── error paths ──────────────────────────────────────────────────────────────
    def test_error_missing_file(self):
        rc, d, _ = self.run_cmd("preview", "--file", str(self.tmp / "nope.zip"))
        self.assertEqual(rc, 1)
        self.assertFalse(d["ok"])
        self.assertIn("file not found", d["error"])

    def test_error_bad_zip(self):
        bad = self.tmp / "bad.zip"
        bad.write_text("this is not a zip")
        rc, d, _ = self.run_cmd("preview", "--file", str(bad))
        self.assertEqual(rc, 1)
        self.assertIn("not a readable zip", d["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
