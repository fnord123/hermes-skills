#!/usr/bin/env python3
"""test_skillpipe.py — white-box tests for the skillpipe transition table.

Runs the pure `decide()` through every edge of the graph: happy path
(scripted + scriptless), every FAIL loop, every cap -> park, desync
detection, and resume counter resets. No network, no git, no gh —
decide() is the whole pipeline in code and is tested here exactly.

Run: python3 tools/../../../skill-kanban/scripts/test_skillpipe.py
House contract: one JSON object on stdout.
"""

import json
import os
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skillpipe import decide, fail  # noqa: E402  # noqa: F401


def state(author=1, ste100=0, scripter=0, infeasible=0) -> dict:
    return {"skill": "demo", "mode": "update", "branch": "sr/demo",
            "worktree": "/tmp/wt/demo", "pr": "https://x/pull/1",
            "author_round": author, "ste100_round": ste100,
            "scripter_round": scripter, "infeasible": infeasible,
            "cards": {}}


def expect(role, N, st, has_scripts, passed, want_target, want_state=None):
    st = dict(st)
    target, detail = decide(role, N, st, has_scripts, passed)
    assert target == want_target, (
        f"decide({role},{N},st={st},hs={has_scripts},pass={passed}) "
        f"-> {target!r}, want {want_target!r}\n  detail: {detail}")
    if want_state:
        for key, value in want_state.items():
            assert st[key] == value, (
                f"decide({role},{N},...) state[{key}] = {st[key]!r}, "
                f"want {value!r}")
    return target, detail


def main() -> None:
    scripted, scriptless = True, False
    cases = 0

    def step(*a, **kw):
        nonlocal cases
        cases += 1
        expect(*a, **kw)

    # -- happy path, scripted skill ------------------------------------
    step("author", 1, state(), scripted, True, "audit-ready-1")
    step("audit", 1, state(), scripted, True, "ste100-ready-1",
         want_state={"ste100_round": 1})
    step("ste100", 1, state(ste100=1), scripted, True, "scripter-ready-1",
         want_state={"scripter_round": 1})
    step("scripter", 1, state(ste100=1, scripter=1), scripted, True,
         "verifier-ready-1")
    step("verifier", 1, state(ste100=1, scripter=1), scripted, True,
         "commit-ready")
    step("commit", 0, state(ste100=1, scripter=1), scripted, True, "MERGED")

    # -- happy path, scriptless skill (audit/ste100 route straight to
    #    commit; author/audit N preserved through the loop) -------------
    step("author", 2, state(author=2), scriptless, True, "audit-ready-2")
    step("audit", 2, state(author=2), scriptless, True, "commit-ready")
    step("ste100", 1, state(ste100=1), scriptless, True, "commit-ready")

    # -- audit FAIL loop: N preserved on PASS, bumped on FAIL -----------
    step("audit", 1, state(), scripted, False, "author-ready-2",
         want_state={"author_round": 2})
    step("author", 2, state(author=2), scripted, True, "audit-ready-2")
    step("audit", 2, state(author=2), scripted, False, "author-ready-3",
         want_state={"author_round": 3})
    step("author", 3, state(author=3), scripted, True, "audit-ready-3")
    # ... rounds 4 and 5 ...
    step("audit", 3, state(author=3), scripted, False, "author-ready-4",
         want_state={"author_round": 4})
    step("audit", 4, state(author=4), scripted, False, "author-ready-5",
         want_state={"author_round": 5})
    # audit FAIL at round 5 = cap: author_round -> 6 > 5 -> park
    step("audit", 5, state(author=5), scripted, False, "parked-audit-5",
         want_state={"author_round": 6})
    # author PASS at round 5 is legal (the 5th proposal gets its audit)
    step("author", 5, state(author=5), scripted, True, "audit-ready-5")

    # -- STE100 FAIL: bounces to author (author_round bumped), parks at 3
    step("ste100", 1, state(ste100=1, author=2), scripted, False,
         "author-ready-3", want_state={"author_round": 3})
    step("ste100", 2, state(ste100=2, author=3), scripted, False,
         "author-ready-4", want_state={"author_round": 4})
    step("ste100", 3, state(ste100=3, author=4), scripted, False,
         "parked-ste100-3")

    # -- STE100 entry cap: audit PASS would make round 4 > 3 -> park ----
    step("audit", 5, state(author=5, ste100=3), scripted, True,
         "parked-ste100-3")

    # -- verifier FAIL loop: scripter_round bumped, parks at 3 ----------
    step("verifier", 1, state(scripter=1), scripted, False,
         "scripter-ready-2", want_state={"scripter_round": 2})
    step("scripter", 2, state(scripter=2), scripted, True, "verifier-ready-2")
    step("verifier", 2, state(scripter=2), scripted, False,
         "scripter-ready-3", want_state={"scripter_round": 3})
    step("verifier", 3, state(scripter=3), scripted, False, "parked-verifier-3",
         want_state={"scripter_round": 4})

    # -- scripter entry cap: ste100 PASS would make round 4 > 3 -> park --
    step("ste100", 3, state(ste100=3, scripter=3), scripted, True,
         "parked-verifier-3")

    # -- scripter FAIL = contract infeasible: one-shot, 2nd = park ------
    step("scripter", 1, state(scripter=1, infeasible=0), scripted, False,
         "author-ready-2", want_state={"author_round": 2, "infeasible": 1})
    step("scripter", 1, state(scripter=1, author=2, infeasible=1), scripted,
         False, "parked-scripter-3",
         want_state={"infeasible": 2})

    # -- author FAIL = request infeasible: parks immediately -------------
    step("author", 1, state(), scripted, False, "parked-author-5")

    # -- commit FAIL = park; resume is an owner verb (not decide()) -----
    step("commit", 0, state(), scripted, False, "parked-commit")

    # -- desync detection: label and state block disagree ----------------
    import contextlib
    import io
    desyncs = 0
    for role, N, st, hs, passed in [
        ("author", 1, state(author=2), scripted, True),
        ("audit", 1, state(author=3), scripted, True),
        ("ste100", 1, state(ste100=2), scripted, True),
        ("scripter", 1, state(scripter=2), scripted, True),
        ("verifier", 1, state(scripter=2), scripted, True),
    ]:
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink):
                decide(role, N, dict(st), hs, passed)
        except SystemExit as exc:
            desyncs += 1
            assert exc.code == 1, f"desync must exit 1, got {exc.code}"
            assert "desync" in sink.getvalue()
        else:
            raise AssertionError(f"desync not detected: {role},{N},{st}")
    assert desyncs == 5, f"expected 5 desync detections, got {desyncs}"

    print(json.dumps({"ok": True, "cases": cases + desyncs,
                      "table": "all edges covered",
                      "desyncs": desyncs}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(json.dumps({"ok": False, "error": f"assertion: {exc}"}))
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"unhandled: {exc!r}"}))
        sys.exit(1)
