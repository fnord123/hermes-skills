#!/usr/bin/env python3
"""test_skillpipe.py — white-box tests for the skillpipe transition table.

Runs the pure `decide()` through every edge of the graph: happy path
(scripted + scriptless), every FAIL loop, every cap -> park, desync
detection, and resume counter resets. No network, no git, no gh —
decide() is the whole pipeline in code and is tested here exactly.

Run: python3 tools/../../../skill-foundry/scripts/skillpipe_test.py
House contract: one JSON object on stdout.
"""

import json
import os
import sys
from typing import NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skillpipe  # noqa: E402  (for patching run / _ROLE_TOKEN in gh tests)
from skillpipe import (  # noqa: E402  # noqa: F401
    decide, fail, all_state_labels, parse_label, READY_PREFIX, LABEL_CAPS,
    PARK_LABELS)


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

    # -- happy path, scriptless skill (audit always routes to ste100;
    #    ste100 routes straight to commit; author/audit N preserved) ----
    step("author", 2, state(author=2), scriptless, True, "audit-ready-2")
    step("audit", 2, state(author=2), scriptless, True, "ste100-ready-1",
         want_state={"ste100_round": 1})
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

    # -- label-vocabulary consistency ----------------------------------
    # Every label the transition table can emit must be recognized by
    # all_state_labels() (the set current_state_label() filters against)
    # and round-trip through parse_label(). This is the layer the pure
    # decide() table doesn't touch — a mismatch there breaks every
    # transition even though the table itself is correct.
    all_labels = all_state_labels()
    for role, prefix in READY_PREFIX.items():
        if role in LABEL_CAPS:
            for i in range(1, LABEL_CAPS[role] + 1):
                lab = f"{prefix}-{i}"
                assert lab in all_labels, f"{lab} not in all_state_labels()"
                assert parse_label(lab) == (role, i), \
                    f"parse_label({lab}) != ({role}, {i})"
    assert "commit-ready" in all_labels
    assert parse_label("commit-ready") == ("commit", 0)
    for lab in PARK_LABELS:
        assert lab in all_labels, f"{lab} not in all_state_labels()"
    # and no stray role-name labels (author-1) may sneak in
    for role in LABEL_CAPS:
        assert f"{role}-1" not in all_labels, \
            f"stray role-name label {role}-1 in all_state_labels()"

    # -- gh actor auth: the GH_APP_ID presence is the only switch
    # gh() is the single seam every gh call funnels through. Only the
    # subprocess boundary (skillpipe.run) is faked, so the real _role_token
    # — including its once-per-process cache — is what gets tested.
    import subprocess as _sp
    captured = {}

    orig_run = skillpipe.run

    def _unpatched(*a, **k):
        raise AssertionError("skillpipe.run left unpatched by a gh test case")
    skillpipe.run = _unpatched
    inst = {"REPO": "owner/repo", "REPO_DIR": "/tmp", "BOARD": "skills"}

    # operator context: no SKILLPIPE vars -> gh runs exactly as found,
    # GH_TOKEN left untouched (the operator's own auth, on purpose).
    def op_run(cmd, cwd=None, check=True):
        captured["cmd"] = cmd
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
    skillpipe.run = op_run
    os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GH_APP_ID", None)
    skillpipe.gh(inst, ["issue", "list"])
    assert captured["cmd"][:1] == ["gh"], captured["cmd"]
    assert "GH_TOKEN" not in os.environ, \
        "operator run must not invent a GH_TOKEN"

    # role context: GH_APP_ID present -> the real _role_token
    # mints once (fake subprocess) and gh() publishes it as GH_TOKEN; a
    # second gh call in the same process must NOT re-mint.
    mints = {"n": 0}

    def role_run(cmd, cwd=None, check=True):
        if cmd[-1] == "token":  # the helper mint call
            mints["n"] += 1
            return _sp.CompletedProcess(cmd, 0,
                                        stdout="ghs_12345_test\n")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
    skillpipe.run = role_run
    skillpipe._ROLE_TOKEN = None
    os.environ["GH_APP_ID"] = "12345"
    os.environ["GH_TOKEN"] = "stale-should-be-replaced"
    skillpipe.gh(inst, ["issue", "comment", "1", "--body", "x"])
    assert os.environ["GH_TOKEN"] == "ghs_12345_test", \
        f"role run must set GH_TOKEN, got {os.environ.get('GH_TOKEN')!r}"
    assert mints["n"] == 1, f"expected 1 mint, got {mints['n']}"
    skillpipe.gh(inst, ["issue", "comment", "2", "--body", "y"])
    assert mints["n"] == 1, "second gh call must reuse the cached token"

    # fail-closed: a mint that fails must exit the script — gh must never
    # proceed with the operator's ambient GH_TOKEN in a role context.
    def dead_mint_run(cmd, cwd=None, check=True):
        if cmd[-1] == "token":
            raise SystemExit(1)
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
    skillpipe.run = dead_mint_run
    skillpipe._ROLE_TOKEN = None
    os.environ["GH_TOKEN"] = "operator-pat"
    try:
        skillpipe.gh(inst, ["issue", "comment", "3", "--body", "z"])
        raise AssertionError("mint failure must exit, not proceed")
    except SystemExit:
        pass
    finally:
        skillpipe.run = orig_run
        skillpipe._ROLE_TOKEN = None
        os.environ.pop("GH_APP_ID", None)
        os.environ.pop("GH_TOKEN", None)
    gh_cases = 3

    # -- pr-open: the PR author must be the role app, not the operator ----
    # The step-2 live proof found a direct `gh pr create` in the worker
    # shell authed as the owner (HOME-based). pr-open routes the create
    # through the same gh() seam, so it mints the role token. We patch
    # the subprocess boundary (run) + the issue read/write boundary so
    # the REAL gh(), _role_token, parse_state, state_block, render_body
    # all execute; only the external calls are captured.
    import subprocess as _sp2
    pr_calls = {}
    edited: dict = {"body": None}

    state_nopr = {"skill": "demo", "mode": "update", "branch": "sr/demo",
                  "worktree": "/tmp/wt/demo", "author_round": 1,
                  "ste100_round": 0, "scripter_round": 0, "infeasible": 0,
                  "cards": {}}
    body_nopr = "# issue\n\n" + skillpipe.state_block(state_nopr)

    def pr_run(cmd, cwd=None, check=True):
        if cmd[-1] == "token":
            return _sp2.CompletedProcess(cmd, 0, stdout="ghs_12345_test\n")
        if cmd[0] == "gh" and cmd[1] == "pr" and cmd[2] == "create":
            pr_calls["cmd"] = cmd
            pr_calls["token"] = os.environ.get("GH_TOKEN")
            return _sp2.CompletedProcess(cmd, 0,
                                         stdout="https://x/pull/42\n")
        return _sp2.CompletedProcess(cmd, 0, stdout="", stderr="")

    orig_run2 = skillpipe.run
    orig_body = skillpipe.issue_body
    orig_edit = skillpipe.edit_issue
    skillpipe.run = pr_run
    skillpipe.issue_body = lambda inst, n: body_nopr
    skillpipe.edit_issue = lambda inst, n, body, **k: edited.__setitem__("body", body)
    skillpipe._ROLE_TOKEN = None
    os.environ["GH_APP_ID"] = "12345"
    os.environ.pop("GH_TOKEN", None)

    class _PrArgs:
        issue, title, head, body, body_file = 42, "demo: add thing", None, None, None
    try:
        try:
            skillpipe.verb_pr_open(inst, _PrArgs())
            raise AssertionError("pr-open must out() (exit 0)")
        except SystemExit as exc:
            assert exc.code == 0, f"pr-open success must exit 0, got {exc.code}"
        # the create went through gh with the minted role token
        assert pr_calls["cmd"][:4] == ["gh", "pr", "create", "--head"], \
            f"pr-open must call gh pr create --head, got {pr_calls['cmd'][:4]}"
        assert pr_calls["token"] == "ghs_12345_test", \
            f"pr-open must auth with the role token, got {pr_calls.get('token')!r}"
        # and it recorded the PR URL into the state block (edit_issue)
        assert "https://x/pull/42" in (edited["body"] or ""), \
            "pr-open must write the PR URL into the issue state block"
        # idempotency: a state that already has a PR must refuse a 2nd
        state_withpr = dict(state_nopr, pr="https://x/pull/42")
        skillpipe.issue_body = lambda inst, n: "# i\n\n" + skillpipe.state_block(state_withpr)
        pr_calls.clear()
        try:
            skillpipe.verb_pr_open(inst, _PrArgs())
            raise AssertionError("pr-open must refuse a second PR")
        except SystemExit as exc:
            assert exc.code == 1, "refusing a 2nd PR must exit 1"
        assert "cmd" not in pr_calls, "no gh pr create on a 2nd-PR refusal"
        # the no-Closes doctrine: a body auto-closing the issue is rejected
        skillpipe.issue_body = lambda inst, n: "# i\n\n" + skillpipe.state_block(state_nopr)
        class _PrArgsClose(_PrArgs):
            body = "Fixes #42"
        try:
            skillpipe.verb_pr_open(inst, _PrArgsClose())
            raise AssertionError("pr-open must reject a Closes #<n> body")
        except SystemExit as exc:
            assert exc.code == 1, "Closes-rejection must exit 1"
    finally:
        skillpipe.run = orig_run2
        skillpipe.issue_body = orig_body
        skillpipe.edit_issue = orig_edit
        skillpipe._ROLE_TOKEN = None
        os.environ.pop("GH_APP_ID", None)
        os.environ.pop("GH_TOKEN", None)
    pr_cases = 3

    # -- abandon removes the REMOTE branch too (and reports truthfully) --
    # The author role PUSHES the branch, so abandon must delete it on
    # origin, not just `git branch -D` locally — and branch_removed must
    # reflect a verified remote state, not an unconditional True (2026-09-07:
    # all three throwaway branches leaked because of the old lie).
    import tempfile as _tf
    import shutil as _sh
    wt_dir = _tf.mkdtemp(prefix="sp-ab-")
    ab_state = {"skill": "demo", "mode": "create", "branch": "sr/demo",
                "worktree": wt_dir, "author_round": 1, "ste100_round": 0,
                "scripter_round": 0, "infeasible": 0,
                "cards": {"author": ["t_aa111111"],
                          "scripter": ["t_bb222222"]}}
    body_ab = "# i\n\n" + skillpipe.state_block(ab_state)

    def make_ab_run(remote_present: bool):
        calls = {"git": [], "push": None, "close": None, "comment": False,
                 "kanban": []}

        def ab_run(cmd, cwd=None, check=True):
            if cmd[0] == "git":
                calls["git"].append(cmd[1:])
                if cmd[1:3] == ["worktree", "remove"]:
                    import shutil as _sh2
                    _sh2.rmtree(cmd[3], ignore_errors=True)
                    return _sp2.CompletedProcess(cmd, 0, stdout="", stderr="")
                if cmd[1:4] == ["push", "origin", "--delete"] and \
                        cmd[-1] == ab_state["branch"]:
                    calls["push"] = cmd
                    return _sp2.CompletedProcess(cmd, 0, stdout="", stderr="")
                if cmd[1:3] == ["ls-remote", "--heads"] and \
                        cmd[-1] == ab_state["branch"]:
                    # probe #1 (or #2 after the delete)
                    deleted = calls["push"] is not None
                    so = "" if (deleted or not remote_present) else \
                        "abc123\trefs/heads/" + ab_state["branch"] + "\n"
                    return _sp2.CompletedProcess(cmd, 0, stdout=so, stderr="")
                return _sp2.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[-1] == "token" and "skillpipe-auth" in cmd[1]:
                return _sp2.CompletedProcess(cmd, 0, stdout="ghs_12345_test\n")
            if cmd[0] == "hermes" and cmd[1] == "kanban":
                calls["kanban"].append(cmd)
                return _sp2.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[0] == "gh":
                if cmd[1:3] == ["issue", "close"]:
                    calls["close"] = cmd
                if "comment" in cmd:
                    calls["comment"] = True
                return _sp2.CompletedProcess(cmd, 0, stdout="", stderr="")
            return _sp2.CompletedProcess(cmd, 0, stdout="", stderr="")
        return ab_run, calls

    orig_run3 = skillpipe.run
    orig_ib3 = skillpipe.issue_body
    orig_lt3 = skillpipe.issue_labels
    os.environ["GH_APP_ID"] = "12345"
    os.environ.pop("GH_TOKEN", None)

    class _AbArgs:
        issue, reason, yes = 42, "test", True
    try:
        # case 1: remote branch present -> must push --delete + verify
        run1, calls1 = make_ab_run(remote_present=True)
        skillpipe.run = run1
        skillpipe.issue_body = lambda inst, n: body_ab
        skillpipe.issue_labels = lambda inst, n: ["ste100-ready-1"]
        try:
            skillpipe.verb_abandon(inst, _AbArgs())
            raise AssertionError("abandon must out()")
        except SystemExit as exc:
            assert exc.code == 0
        assert calls1["close"] and calls1["close"][:3] == ["gh", "issue", "close"], \
            "abandon must close the issue"
        assert calls1["comment"], "abandon must post the ABANDONED note"
        assert calls1["push"] is not None, \
            "abandon must push --delete the REMOTE branch"
        assert [c for c in calls1["git"] if c[:2] == ["branch", "-D"]] == \
            [["branch", "-D", ab_state["branch"]]], "local branch delete"
        # abandon must sweep the run's cards too (archive, then the
        # permanent archive --rm) - blocked, done, everything (owner rule
        # 2026-09-11: no leftover cards on an abandoned run)
        arch_calls = [c for c in calls1["kanban"] if "--rm" not in c]
        rm_calls = [c for c in calls1["kanban"] if "--rm" in c]
        assert len(arch_calls) == 1 and len(rm_calls) == 1, \
            "abandon must archive then archive --rm the run's cards"
        assert arch_calls[0][-3:] == ["archive", "t_aa111111", "t_bb222222"], \
            "abandon must archive every card in the state block"
        assert rm_calls[0][-4:] == ["archive", "--rm", "t_aa111111",
                                     "t_bb222222"], \
            "abandon must permanently delete the cards (archive --rm)"
        assert not os.path.isdir(wt_dir), "worktree must be removed"
        # case 2: remote branch already gone -> no push, still True
        run2, calls2 = make_ab_run(remote_present=False)
        skillpipe.run = run2
        try:
            skillpipe.verb_abandon(inst, _AbArgs())
            raise AssertionError("abandon must out()")
        except SystemExit as exc:
            assert exc.code == 0
        assert calls2["push"] is None, \
            "no push --delete when the remote branch is already gone"
        assert [c for c in calls2["git"]
                if c[:3] == ["ls-remote", "--heads", "origin"]], \
            "still probes the remote before deciding"
        assert not [c for c in calls2["git"]
                    if c[:3] == ["push", "origin", "--delete"]], \
            "no delete attempt when the probe found nothing"
    finally:
        skillpipe.run = orig_run3
        skillpipe.issue_body = orig_ib3
        skillpipe.issue_labels = orig_lt3
        skillpipe._ROLE_TOKEN = None
        os.environ.pop("GH_APP_ID", None)
        os.environ.pop("GH_TOKEN", None)
        _sh.rmtree(wt_dir, ignore_errors=True)
    ab_cases = 2

    # -- effective_has_scripts: the route-to-scripter OR --------------
    # declares OR branch. (declares, branch) -> expected; the git seam
    # is mocked per-case so each row is isolated. The ls-tree probe runs
    # only when declares is falsy (the `or` short-circuits on True).
    import subprocess as _sp3
    ehs_cases = 0
    orig_git3 = skillpipe.git
    try:
        for declares, branch, expected, want_git in [
            (False, False, False, True),   # create, no contract: probe -> none
            (True, False, True, False),    # create + contract: short-circuit,
                                           # no git call at all
            (False, True, True, True),     # update mode: probe finds scripts
            (True, True, True, False),     # both: short-circuit (already True)
        ]:
            calls = {"git": 0}

            def ehs_git(inst, args, check=True, **_kw):
                if args[:1] == ["fetch"]:
                    return _sp3.CompletedProcess(
                        args, 0, stdout="", stderr="")
                calls["git"] += 1
                so = "demo/scripts/x.py\n" if branch else ""
                return _sp3.CompletedProcess(
                    args, 0, stdout=so, stderr="")

            skillpipe.git = ehs_git
            got = skillpipe.effective_has_scripts(
                {"REPO_DIR": "/tmp"}, "sr/demo", "demo", declares)
            assert got == expected, (
                f"effective_has_scripts(declares={declares}, "
                f"branch={branch}) -> {got}, want {expected}")
            assert (calls["git"] > 0) == want_git, (
                f"git seam called={calls['git'] > 0}, want {want_git} "
                f"(declares={declares})")
            ehs_cases += 1
    finally:
        skillpipe.git = orig_git3

    # -- state round-trip: the flag is carried by the issue block -----
    rt_state = {"skill": "demo", "mode": "create", "branch": "sr/demo",
                "author_round": 1, "ste100_round": 0,
                "scripter_round": 0, "infeasible": 0,
                "cards": {}, "declares_scripts": True}
    rt_body = "# i\n\n" + skillpipe.state_block(rt_state)
    rt_parsed = skillpipe.parse_state(rt_body)
    assert rt_parsed.get("declares_scripts") is True, \
        "declares_scripts must round-trip through the state block"
    rt_cases = 1

    # -- dispatch card carries the work-order-supremacy rule ----------
    # The card is the text the worker reads first; the binding rule
    # must render in it (regression for the abandoned-run anchor).
    import subprocess as _sp4
    cb_cases = 0
    inst2 = dict(inst)
    inst2["ASSIGNEE"] = "author=worker-x"
    inst2["WORKTREE_ROOT"] = "/wts"
    import tempfile as _tf
    _cards = _tf.mkdtemp()
    open(os.path.join(_cards, "author-role.md"), "w").write("stub")
    inst2["CARDS_DIR"] = _cards
    captured = {}

    def _cap(cmd, *a, **k):
        captured["cmd"] = cmd
        return _sp4.CompletedProcess(cmd, 0,
                                     stdout=json.dumps({"id": "t_cb"}),
                                     stderr="")

    orig_run2 = skillpipe.run
    skillpipe.run = _cap
    try:
        skillpipe.kanban_create(inst2, "author", 33, "author-ready-1",
                                "github-issue-pr", "/wt", "sr-github-issue-pr-i33")
    finally:
        skillpipe.run = orig_run2
    body_path = None
    for i, tok in enumerate(captured.get("cmd", [])):
        if tok == "--body" and i + 1 < len(captured["cmd"]):
            body_path = captured["cmd"][i + 1]
    if body_path is None:
        raise AssertionError("kanban_create did not pass --body")
    cb = " ".join(body_path.split())
    assert "The new issue body is the ONLY work order: prior runs " \
           "of this skill are out of scope" in cb, \
        "dispatch card body must carry the work-order-supremacy rule"
    assert f"GitHub issue #33" in cb and "author-ready-1" in cb
    cb_cases = 1

    # -- merge preflight: the rev-list count is TAB-separated -----------
    # git emits "0\t0" (verified via od -c); a raw comparison to the
    # string "0 0" can never hold, so every merge parked as a false
    # positive (issue #35, parked-commit). The preflight must accept
    # the real git output and still catch a true divergence.
    import subprocess as _sp5
    pf_cases = 0
    orig_git5 = skillpipe.git
    inst3 = {"REPO_DIR": "/tmp"}
    try:
        for count, want_clean in [
            ("0\t0\n", True),    # in sync — git's real tab-separated form
            ("0 0\n", True),     # in sync — spaced form tolerated too
            ("1\t0\n", False),   # main ahead of origin/main
            ("0\t1\n", False),   # main behind origin/main
        ]:
            def pf_git(inst, args, check=True, **_kw):
                if args[:1] == ["status"]:
                    return _sp5.CompletedProcess(args, 0, stdout="",
                                                 stderr="")
                if args[:1] == ["branch"]:
                    return _sp5.CompletedProcess(args, 0, stdout="main\n",
                                                 stderr="")
                if args[:1] == ["rev-list"]:
                    return _sp5.CompletedProcess(args, 0, stdout=count,
                                                 stderr="")
                return _sp5.CompletedProcess(args, 0, stdout="", stderr="")

            skillpipe.git = pf_git
            reason = skillpipe._merge_preflight(inst3, {"skill": "demo"})
            if want_clean:
                assert reason == "", (
                    f"_merge_preflight(count={count!r}) -> {reason!r}, "
                    "want clean (a false positive parks every merge)")
            else:
                assert "diverged" in reason, (
                    f"_merge_preflight(count={count!r}) -> {reason!r}, "
                    "want a divergence reason")
            pf_cases += 1
    finally:
        skillpipe.git = orig_git5

    print(json.dumps({"ok": True,
                      "cases": (cases + desyncs + gh_cases + pr_cases
                                + ab_cases + ehs_cases + rt_cases + cb_cases
                                + pf_cases),
                      "table": "all edges covered",
                      "desyncs": desyncs,
                      "gh_actor": gh_cases,
                      "pr_open": pr_cases,
                      "abandon": ab_cases,
                      "declares_scripts": ehs_cases + rt_cases,
                      "card_body": cb_cases,
                      "merge_preflight": pf_cases}))
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
