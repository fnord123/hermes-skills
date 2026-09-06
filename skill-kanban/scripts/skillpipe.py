#!/usr/bin/env python3
"""skillpipe.py — state machine for the PR-based skill review pipeline.

The SINGLE writer of pipeline state: GitHub labels, the issue state block,
PR/issue comments, and kanban dispatch cards. Pipeline roles never touch
labels, issue bodies, or kanban cards directly — they do their stage work
(review the PR, rework it, verify it) and then call exactly ONE verb here
per completed stage. This script is the only thing that mutates state, so
the transition table in `decide()` is the entire pipeline, enforced in
code rather than in LLM text.

Artifacts:
  - One GitHub ISSUE per skill request = state + work order. Its state
    label is the current stage; a fenced state block at the bottom of its
    body carries round counters and the PR/branch/worktree pointers.
  - One long-lived GitHub PR per pipeline = the artifact trail. Every
    role verdict (PASS summary or FAIL findings) is posted to the PR as a
    comment by this script, so the PR reads top-to-bottom as the whole
    review record.
  - Kanban cards = dumb dispatch. Each transition creates exactly one
    ready card whose body says: read your role playbook, look at issue
    #N (and the PR if any). No parentage, no handoff payloads — the issue
    and the PR are the handoff.

State labels (exactly one present on the issue at a time):
  author-ready-1..5   author drafting/reworking the PR
  audit-ready-1..5    audit reviewing the PR (N preserved from author)
  ste100-ready-1..3   STE100 writing audit of the PR
  scripter-ready-1..3 scripter implementing the script contract on the PR
  verifier-ready-1..3 verifier running the test matrix against the PR
  commit-ready        merge the PR
Park labels (loop cap exhausted — owner resumes or abandons):
  parked-author-5     author declared the request itself infeasible
  parked-audit-5      author/audit loop exhausted (5 rounds)
  parked-ste100-3     STE100 loop exhausted (3 rounds)
  parked-scripter-3   contract infeasibility declared twice
  parked-verifier-3   scripter/verifier loop exhausted (3 rounds)
  parked-commit       merge pre-flight failed; owner fixes, re-runs merge
Tracking labels (persist for the life of the issue):
  pipeline, skill-<slug>

Verbs:
  intake      open issue + worktree + author card for ONE skill
  intake-all  intake every skill in the repo (or --skills a,b,c)
  transition  a role finished: relabel, update state, PR comment, next card
  merge       merge the PR, close the issue, dispatch fleet card
  resume      owner: parked issue back to ANY ready label (+ your comment)
  comment     post a note to the PR (or issue)
  status      one issue: label, state, PR, worktree
  list        every open pipeline issue
  abandon     close the issue, remove the worktree + branch

House contract: exactly ONE JSON object on stdout per call. Success is
{"ok": true, ...}; failure is {"ok": false, "error": "..."} + exit 1.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from typing import NoReturn, Optional

STATE_BLOCK_RE = re.compile(r"<!-- pipeline-state\n(.*?)\npipeline-state -->",
                            re.DOTALL)
LABEL_CAPS = {"author": 5, "audit": 5, "ste100": 3, "scripter": 3,
              "verifier": 3}
READY_PREFIX = {"author": "author-ready", "audit": "audit-ready",
                "ste100": "ste100-ready", "scripter": "scripter-ready",
                "verifier": "verifier-ready", "commit": "commit-ready"}
PARK_LABELS = ["parked-author-5", "parked-audit-5", "parked-ste100-3",
               "parked-scripter-3", "parked-verifier-3", "parked-commit"]
LABEL_COLORS = {"pipeline": "1d76db", "author": "0366d6", "audit": "005ccc",
                "ste100": "391cba", "scripter": "1a7f37", "verifier": "2da124",
                "commit": "fbca04", "parked": "d73a4a", "skill": "92c37d"}
MID_ROLES = ("author", "scripter")

# ---------------------------------------------------------------- helpers


def fail(message: str) -> NoReturn:
    print(json.dumps({"ok": False, "error": message}))
    sys.exit(1)


def out(payload: dict) -> NoReturn:
    print(json.dumps({"ok": True, **payload}))
    sys.exit(0)


def run(cmd: list, cwd: str = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=300)
    except FileNotFoundError as exc:
        fail(f"command not found: {cmd[0]} ({exc})")
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        fail(f"command failed ({proc.returncode}): {shlex.join(cmd)}\n{detail[-2000:]}")
    return proc


def gh(inst: dict, args: list, check: bool = True) -> subprocess.CompletedProcess:
    return run(["gh", *args, "--repo", inst["REPO"]], check=check)


def gh_json(inst: dict, args: list):
    proc = gh(inst, args)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail(f"gh returned non-JSON for: gh {' '.join(args)}\n{proc.stdout[:500]}")


def git(inst: dict, args: list, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=inst["REPO_DIR"], check=check)


def write_tmp(content: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
    handle.write(content)
    handle.close()
    return handle.name


def parse_instance(path: str) -> dict:
    if not path or not os.path.isfile(path):
        fail(f"instance file not found: {path!r} (pass --instance or set SKILLPIPE_INSTANCE)")
    inst = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        inst[key.strip()] = value.strip()
    missing = [k for k in ("REPO_DIR", "REPO", "BOARD", "WORKTREE_ROOT",
                           "CARDS_DIR", "ASSIGNEE") if not inst.get(k)]
    if missing:
        fail(f"instance file {path} missing keys: {', '.join(missing)}")
    return inst


def assignee_for(inst: dict, role: str) -> str:
    spec = inst["ASSIGNEE"]
    if "=" not in spec:
        return spec
    for entry in spec.split(","):
        if "=" in entry:
            role_name, profile = [p.strip() for p in entry.split("=", 1)]
            if role_name == role:
                return profile
    fail(f"ASSIGNEE map has no entry for role '{role}': {spec}")


def slug(skill: str) -> str:
    return skill.strip().strip("/")


def parse_state(body: str) -> dict:
    match = STATE_BLOCK_RE.search(body)
    if not match:
        fail("issue body has no pipeline-state block")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        fail("pipeline-state block is not valid JSON")


def state_block(state: dict) -> str:
    return ("<!-- pipeline-state\n" +
            json.dumps(state, indent=1, sort_keys=True) +
            "\npipeline-state -->")


def render_body(body: str, state: dict, note: str = None) -> str:
    """Append a round note (above the state block) and refresh the block."""
    block = state_block(state)
    match = STATE_BLOCK_RE.search(body)
    if match:
        head, tail = body[:match.start()].rstrip(), body[match.end():].strip()
    else:
        head, tail = body.rstrip(), ""
    if note:
        head = head + "\n" + note
    result = head + "\n\n" + block
    if tail:
        result += "\n\n" + tail
    return result + "\n"


def all_state_labels() -> set:
    ready = {f"{prefix}-{i}" for prefix, cap in
             ((p, LABEL_CAPS[p]) for p in LABEL_CAPS) for i in range(1, cap + 1)}
    return ready | {"commit-ready"} | set(PARK_LABELS)


def issue_data(inst: dict, n: int) -> dict:
    return gh_json(inst, ["issue", "view", str(n),
                          "--json", "number,title,body,labels,state"])


def issue_labels(inst: dict, n: int) -> list:
    return [label["name"] for label in issue_data(inst, n)["labels"]]


def issue_body(inst: dict, n: int) -> str:
    return issue_data(inst, n)["body"]


def current_state_label(inst: dict, n: int) -> str:
    present = [l for l in issue_labels(inst, n) if l in all_state_labels()]
    if len(present) != 1:
        fail(f"issue {n} has {len(present)} state labels (expected 1): {present}")
    return present[0]


def parse_label(label: str):
    """'author-ready-3' -> ('author', 3); 'commit-ready' -> ('commit', 0)."""
    if label == "commit-ready":
        return "commit", 0
    for role, prefix in READY_PREFIX.items():
        if label.startswith(prefix + "-"):
            return role, int(label.rsplit("-", 1)[1])
    fail(f"not a ready label: {label}")


def target_role_of(label: str) -> str:
    role, _ = parse_label(label)
    return role


def ensure_labels(inst: dict) -> None:
    # --limit: gh list commands page at 30; the pipeline needs the FULL
    # label set or the exists-check is wrong and create hits "already
    # exists".
    existing = {l["name"] for l in
                gh_json(inst, ["label", "list", "--json", "name",
                               "--limit", "200"])}
    wanted = set(PARK_LABELS) | {"pipeline"}
    for role, prefix in READY_PREFIX.items():
        if role in LABEL_CAPS:  # commit-ready is terminal: no cap, no number
            wanted |= {f"{prefix}-{i}" for i in range(1, LABEL_CAPS[role] + 1)}
    wanted.add("commit-ready")
    for name in sorted(wanted):
        if name not in existing:
            family = ("parked" if name.startswith("parked-")
                      else name.split("-")[0])
            # --force: idempotent — a concurrent intake or a prior partial
            # run may have created it between the list and the create.
            gh(inst, ["label", "create", name, "--force",
                      f"--color={LABEL_COLORS.get(family, 'cccccc')}",
                      f"--description=skillpipe state: {name}"])


def pr_number(pr_url: str) -> int:
    match = re.search(r"/pull/(\d+)", pr_url or "")
    if not match:
        fail(f"cannot parse PR number from: {pr_url}")
    return int(match.group(1))


def branch_has_scripts(inst: dict, branch: str, skill: str) -> bool:
    git(inst, ["fetch", "origin", branch], check=False)
    proc = git(inst, ["ls-tree", "-r", "--name-only", f"origin/{branch}",
                      "--", f"{skill}/scripts/"], check=False)
    return bool(proc.stdout.strip())


def post_pr_comment(inst: dict, pr_url: str, markdown: str) -> None:
    path = write_tmp(markdown)
    try:
        gh(inst, ["pr", "comment", str(pr_number(pr_url)), "--body-file", path])
    finally:
        os.unlink(path)


def post_issue_comment(inst: dict, n: int, markdown: str) -> None:
    path = write_tmp(markdown)
    try:
        gh(inst, ["issue", "comment", str(n), "--body-file", path])
    finally:
        os.unlink(path)


def issue_create(inst: dict, title: str, body: str, labels: list) -> tuple:
    """gh issue create prints a bare URL (no --json). The labels MUST
    pre-exist or the create fails outright — the caller bootstraps them."""
    path = write_tmp(body)
    args = ["issue", "create", "--title", title, "--body-file", path]
    for label in labels:
        args += ["--label", label]
    try:
        proc = gh(inst, args)
    finally:
        os.unlink(path)
    url = proc.stdout.strip().splitlines()[-1].strip()
    match = re.search(r"/issues/(\d+)", url)
    if not match:
        fail(f"issue create did not return a URL: {url!r}")
    return int(match.group(1)), url


def edit_issue(inst: dict, n: int, body: str,
               add_label: Optional[str] = None,
               remove_labels: Optional[list] = None) -> None:
    path = write_tmp(body)
    args = ["issue", "edit", str(n), "--body-file", path]
    if add_label:
        # --add-label is additive: it keeps the tracking labels (pipeline,
        # skill-<slug>) and adds the new state label.
        args += ["--add-label", add_label]
    for label in (remove_labels or []):
        args += ["--remove-label", label]
    try:
        gh(inst, args)
    finally:
        os.unlink(path)


def idem_key(state: dict, n: int, role: str, round_no: int) -> str:
    return f"sr-{state['skill']}-i{n}-{role}-r{round_no}"


def kanban_create(inst: dict, role: str, n: int, label: str, skill: str,
                  worktree: str, idem: str) -> dict:
    profile = assignee_for(inst, role)
    runtime = inst.get(f"RUNTIME_{role.upper()}", "30m")
    model = inst.get("MID_MODEL") if role in MID_ROLES else None
    script_path = os.path.realpath(__file__)
    instance_path = inst.get("INSTANCE_PATH", "(not set)")
    playbook = os.path.join(inst["CARDS_DIR"], f"{role}-role.md")
    if not os.path.isfile(playbook):
        fail(f"playbook missing for role {role}: {playbook} "
             f"(check CARDS_DIR in the instance file)")
    body = f"""PIPELINE CARD — role {role}, issue #{n}, skill {skill}
State label: {label} · Repo: {inst['REPO']}
Worktree (this card's dir): {worktree}

WORK ORDER (binding):
1. Read your playbook: {inst['CARDS_DIR']}/{role}-role.md — it defines the
   stage work, its evidence requirements, and the exact script call.
2. The work order for THIS run is the body of GitHub issue #{n} on
   {inst['REPO']} (request text + round notes; state block at the bottom
   carries the PR URL, branch, and round counters). Read it before acting.
3. Do the stage work on the PR/branch named in the state block.
4. Finish with EXACTLY ONE script call (the playbook names it):
   python3 {script_path} --instance {instance_path} <verb> ...
   State changes go ONLY through the script. Never edit labels, issue
   bodies, or create kanban cards yourself — the script owns all three.
5. When the script call succeeds, complete this card with a one-line
   summary (issue number + verdict). The script already created the next
   card. If the script parks the issue, block this card needs_input."""
    cmd = ["hermes", "kanban", "--board", inst["BOARD"], "create",
           f"{role.capitalize()}: {skill} issue #{n} [{label}]",
           "--body", body,
           "--assignee", profile,
           "--workspace", f"dir:{worktree}",
           "--idempotency-key", idem,
           "--max-runtime", runtime,
           "--created-by", inst.get("CREATED_BY", "skillpipe"),
           "--skill", inst.get("HOUSE_SKILL", "hermes-skills-repo"),
           "--json"]
    if model:
        cmd += ["--model", model]
    if role == "ste100" and inst.get("STD_SKILL"):
        # the writing standard is force-loaded next to the house skill
        cmd += ["--skill", inst["STD_SKILL"]]
    proc = run(cmd)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail(f"kanban create returned non-JSON:\n{proc.stdout[:500]}")
    task = data[0] if isinstance(data, list) else data
    return {"id": task.get("id"), "assignee": profile, "title": task.get("title")}


# ------------------------------------------------- the transition table


def decide(role: str, N: int, state: dict,
           has_scripts: Optional[bool], passed: bool):
    """The entire pipeline, in code. Pure except for fail().

    role/N = the issue's CURRENT state label (author-ready-3 -> author/3).
    state  = the state block (mutated: counters).
    has_scripts = branch has skill/scripts/ (only audit and ste100 use it).
    Returns (target, detail) where target is a ready/park label or the
    sentinel "MERGED". detail is the one-line summary for the issue note.
    """
    caps = LABEL_CAPS

    def author_target() -> str:
        if state["author_round"] <= caps["author"]:
            return f"author-ready-{state['author_round']}"
        return "parked-audit-5"

    if role == "author":
        if state["author_round"] != N:
            fail(f"state/label desync: label author-ready-{N} but "
                 f"author_round={state['author_round']}")
        if not passed:
            return ("parked-author-5",
                    "request infeasible — author cannot produce a proposal")
        return (f"audit-ready-{N}",
                f"proposal on PR #{pr_number(state['pr'])}")

    if role == "audit":
        if state["author_round"] != N:
            fail(f"state/label desync: label audit-ready-{N} but "
                 f"author_round={state['author_round']}")
        if not passed:
            state["author_round"] += 1
            return (author_target(),
                    f"FAIL round {N} — findings posted; author_round -> "
                    f"{state['author_round']}")
        if has_scripts:
            state["ste100_round"] += 1
            if state["ste100_round"] > caps["ste100"]:
                return ("parked-ste100-3",
                        "STE100 round budget exhausted on entry")
            return (f"ste100-ready-{state['ste100_round']}",
                    f"PASS — route STE100 (round "
                    f"{state['ste100_round']}/{caps['ste100']})")
        return ("commit-ready", "PASS — script-less, route commit")

    if role == "ste100":
        if state["ste100_round"] != N:
            fail(f"state/label desync: label ste100-ready-{N} but "
                 f"ste100_round={state['ste100_round']}")
        if not passed:
            if N >= caps["ste100"]:
                return ("parked-ste100-3",
                        f"FAIL round {N} — cap {caps['ste100']} exhausted")
            state["author_round"] += 1
            return (author_target(),
                    f"FAIL round {N} — findings posted; author_round -> "
                    f"{state['author_round']}")
        if has_scripts:
            if state["scripter_round"] + 1 > caps["verifier"]:
                return ("parked-verifier-3",
                        "scripter round budget exhausted on entry")
            state["scripter_round"] += 1
            return (f"scripter-ready-{state['scripter_round']}",
                    f"PASS — route scripter (round {state['scripter_round']})")
        return ("commit-ready", "PASS — script-less, route commit")

    if role == "scripter":
        if state["scripter_round"] != N:
            fail(f"state/label desync: label scripter-ready-{N} but "
                 f"scripter_round={state['scripter_round']}")
        if not passed:  # scripter's FAIL = contract infeasible
            state["infeasible"] = state.get("infeasible", 0) + 1
            if state["infeasible"] >= 2:
                return ("parked-scripter-3",
                        "contract infeasibility declared twice — owner decides")
            state["author_round"] += 1
            return (author_target(),
                    f"contract infeasible (declaration {state['infeasible']}/2) "
                    f"— author_round -> {state['author_round']}")
        return (f"verifier-ready-{N}",
                "scripts + test matrix on PR")

    if role == "verifier":
        if state["scripter_round"] != N:
            fail(f"state/label desync: label verifier-ready-{N} but "
                 f"scripter_round={state['scripter_round']}")
        if not passed:
            state["scripter_round"] += 1
            if state["scripter_round"] > caps["verifier"]:
                return ("parked-verifier-3",
                        f"FAIL round {N} — cap {caps['verifier']} exhausted")
            return (f"scripter-ready-{state['scripter_round']}",
                    f"FAIL round {N} — findings posted; scripter_round -> "
                    f"{state['scripter_round']}")
        return ("commit-ready", "PASS — test matrix green")

    if role == "commit":
        return ("MERGED" if passed else "parked-commit",
                "merge" if passed else "merge pre-flight failed — owner fixes, re-runs")

    fail(f"unknown role: {role}")


# ---------------------------------------------------------------- verbs


def dirty_target(inst: dict, skill: str) -> list:
    proc = git(inst, ["status", "--short"])
    return [l for l in proc.stdout.splitlines() if l.strip()
            and (l[3:].strip() == skill
                 or l[3:].strip().startswith(skill + "/"))]


def intake_plan(inst: dict, skill: str, mode_arg: str) -> dict:
    """Compute everything intake would do. Pure reads."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill):
        fail(f"bad skill name: {skill}")
    if mode_arg in ("create", "update"):
        mode = mode_arg
    else:
        proc = git(inst, ["ls-files", f"{skill}/SKILL.md"])
        mode = "update" if proc.stdout.strip() else "create"
    dirty = dirty_target(inst, skill)
    open_issues = gh_json(inst, ["issue", "list", "--label", "pipeline",
                                 "--state", "open", "--limit", "200",
                                 "--json", "number,title,labels"])
    in_flight = None
    for issue in open_issues:
        if f"skill-{skill}" in [l["name"] for l in issue["labels"]]:
            in_flight = issue["number"]
            break
    branch = f"sr/{skill}"
    worktree = os.path.join(inst["WORKTREE_ROOT"], skill)
    return {"skill": skill, "mode": mode, "dirty": dirty,
            "in_flight": in_flight, "branch": branch, "worktree": worktree}


def verb_intake(inst: dict, args) -> None:
    plan = intake_plan(inst, slug(args.skill), args.mode)
    skill = plan["skill"]
    if args.dry_run:
        out({"dry_run": True, **plan,
             "would_label": "author-ready-1",
             "would_card": f"author ({assignee_for(inst, 'author')})"})
    if plan["dirty"]:
        fail(f"target skill dir dirty on main (commit or revert first): "
             f"{plan['dirty']}")
    if plan["in_flight"] is not None:
        fail(f"pipeline already in flight for {skill}: issue "
             f"#{plan['in_flight']}")
    if os.path.exists(plan["worktree"]):
        fail(f"worktree path already exists: {plan['worktree']} "
             f"(stale pipeline? run abandon first)")
    ensure_labels(inst)
    skill_label = f"skill-{skill}"
    if skill_label not in {l["name"] for l in
                           gh_json(inst, ["label", "list", "--json", "name",
                                          "--limit", "200"])}:
        gh(inst, ["label", "create", skill_label, "--force",
                  f"--color={LABEL_COLORS['skill']}",
                  f"--description=skillpipe pipeline for {skill}"])
    request = args.request.strip()
    state = {"skill": skill, "mode": plan["mode"], "branch": plan["branch"],
             "worktree": plan["worktree"], "pr": None,
             "author_round": 1, "ste100_round": 0, "scripter_round": 0,
             "infeasible": 0, "cards": {}}
    body = (f"## Request\n\n{request}\n\n"
            f"## Mode\n\n{plan['mode']} (target: `{skill}/`)\n\n"
            f"## Work order\n\n"
            f"- Branch: `{plan['branch']}` · worktree: `{plan['worktree']}`\n"
            f"- PR: (none yet — the author creates it)\n\n"
            f"## Round notes\n\n(none yet)\n\n"
            + state_block(state) + "\n")
    title = f"{skill}: {plan['mode']} — {request.splitlines()[0][:80]}"
    # create the issue BEFORE the worktree: an issue is cheap to close, a
    # leaked worktree is not. If the worktree add fails, close the issue.
    n, issue_url = issue_create(inst, title, body,
                                ["pipeline", skill_label, "author-ready-1"])
    os.makedirs(inst["WORKTREE_ROOT"], exist_ok=True)
    try:
        git(inst, ["fetch", "origin"])
        git(inst, ["worktree", "add", plan["worktree"], "-b", plan["branch"],
                   "origin/main"])
    except SystemExit:
        gh(inst, ["issue", "close", str(n)], check=False)
        raise
    card = kanban_create(inst, "author", n, "author-ready-1", skill,
                         plan["worktree"], idem_key(state, n, "author", 1))
    state["cards"]["author"] = [card["id"]]
    edit_issue(inst, n, render_body(body, state))
    out({"issue": n, "issue_url": issue_url, "mode": plan["mode"],
         "branch": plan["branch"], "worktree": plan["worktree"],
         "label": "author-ready-1", "card": card})


def verb_intake_all(inst: dict, args) -> None:
    instance_path = inst["INSTANCE_PATH"]
    if args.skills:
        skills = [slug(s) for s in args.skills.split(",") if slug(s)]
    else:
        proc = git(inst, ["ls-files", "*/SKILL.md"])
        skills = sorted({line.split("/", 1)[0]
                         for line in proc.stdout.splitlines() if line.strip()})
    results, errors = [], []
    for skill in skills:
        proc = run([sys.executable, os.path.realpath(__file__),
                    "--instance", instance_path, "intake",
                    "--skill", skill, "--mode", args.mode,
                    "--request", args.request], check=False)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {"ok": False, "error": (proc.stdout or proc.stderr)[:500]}
        if payload.get("ok"):
            results.append({"skill": skill, "issue": payload["issue"],
                            "card": payload["card"]["id"]})
        else:
            errors.append({"skill": skill,
                           "error": payload.get("error", "unknown")})
    out({"requested": len(skills), "intaked": len(results),
         "intaked_skills": results, "failed": errors})


def _park_commit(inst: dict, n: int, state: dict, pr, reason: str) -> dict:
    """Merge pre-flight failed: park, explain, stop (no fleet card).
    Returns the park payload; the caller emits it and, on a parked commit
    card, re-dispatches the commit role."""
    if pr:
        post_pr_comment(inst, pr, f"## COMMIT — PARK\n\n{reason}\n")
    edit_issue(inst, n,
               render_body(issue_body(inst, n), state,
                           note=f"### commit — PARK ({reason.splitlines()[0]})\n"),
               add_label="parked-commit",
               remove_labels=["commit-ready"])
    return {"issue": n, "status": "parked", "parked": "parked-commit",
            "reason": reason}


def _merge_preflight(inst: dict, state: dict) -> str:
    dirty = dirty_target(inst, state["skill"])
    if dirty:
        return f"target skill dir dirty on main: {dirty}"
    branch = git(inst, ["branch", "--show-current"]).stdout.strip()
    if branch != "main":
        return f"main worktree is on branch '{branch}', not main"
    git(inst, ["fetch", "origin"])
    ahead_behind = git(inst, ["rev-list", "--left-right", "--count",
                              "main...origin/main"]).stdout.strip()
    if ahead_behind != "0 0":
        return f"main diverged from origin/main: {ahead_behind}"
    return ""


def _merge_cleanup(inst: dict, state: dict) -> None:
    if os.path.isdir(state["worktree"]):
        git(inst, ["worktree", "remove", state["worktree"], "--force"],
            check=False)
    git(inst, ["branch", "-D", state["branch"]], check=False)
    git(inst, ["fetch", "origin"])
    git(inst, ["merge", "--ff-only", "origin/main"])


def do_merge(inst: dict, n: int, state: dict, pr: str) -> dict:
    reason = _merge_preflight(inst, state)
    if reason:
        return _park_commit(inst, n, state, pr, reason)
    num = pr_number(pr)
    data = gh_json(inst, ["pr", "view", str(num),
                          "--json", "url,state,mergeStateStatus"])
    if data["state"] != "OPEN":
        return _park_commit(inst, n, state, pr,
                            f"PR #{num} is {data['state']}, not OPEN")
    status = data["mergeStateStatus"]
    if status == "BEHIND":
        # catch the branch up to main, then re-read
        git(inst, ["fetch", "origin"])
        git(inst, ["-C", state["worktree"], "merge", "--ff-only",
                   "origin/main"])
        git(inst, ["-C", state["worktree"], "push", "origin",
                   state["branch"]])
        data = gh_json(inst, ["pr", "view", str(num),
                              "--json", "state,mergeStateStatus"])
        status = data["mergeStateStatus"]
    if status not in ("CLEAN", "UNSTABLE"):
        return _park_commit(inst, n, state, pr,
                            f"PR #{num} merge state: {status}")
    gh(inst, ["pr", "merge", str(num), "--squash", "--delete-branch"])
    merged = gh_json(inst, ["pr", "view", str(num),
                            "--json", "mergeCommit,url"])
    sha = merged["mergeCommit"]["oid"]
    # close the issue: the PR body carries no Closes #N, so close is
    # script-controlled (merge = work order complete)
    gh(inst, ["issue", "close", str(n)])
    _merge_cleanup(inst, state)
    return {"merged": True, "pr": merged["url"], "sha": sha,
            "issue_closed": True}


def verb_transition(inst: dict, args) -> None:
    n = args.issue
    cur = current_state_label(inst, n)
    if cur.startswith("parked-"):
        fail(f"issue {n} is parked ({cur}); the owner resumes it with `resume`")
    role, N = parse_label(cur)
    if args.role and args.role != role:
        fail(f"--role {args.role} does not match the issue's label ({cur})")
    if args.pass_ == args.fail:
        fail("exactly one of --pass / --fail is required")
    state = parse_state(issue_body(inst, n))
    if args.pr:
        state["pr"] = args.pr
    findings = None
    if args.fail:
        findings = (open(args.findings_file).read().strip()
                    if args.findings_file else (args.findings_text or "").strip())
        if not findings:
            fail("--fail requires --findings-file or --findings-text")
    if role in ("author", "scripter") and args.pass_ and not state.get("pr"):
        fail("PASS requires a PR (pass --pr URL the first time)")
    has_scripts = (branch_has_scripts(inst, state["branch"], state["skill"])
                   if role in ("audit", "ste100") else None)
    target, detail = decide(role, N, state, has_scripts, args.pass_)
    if target == "MERGED":
        result = do_merge(inst, n, state, state["pr"])
        if result.get("status") == "parked":
            # parked-commit: pre-flight conflict. No new card — this commit
            # card blocks needs_input (see commit-role.md); the owner fixes
            # the shared tree, then `resume` sends the issue back to
            # commit-ready and the commit role re-runs `merge`.
            out({"issue": n, "from": cur, "to": "parked-commit",
                 "block_this_card": "needs_input", **result})
        card = kanban_create(inst, "fleet", n, "commit-ready", state["skill"],
                             inst["REPO_DIR"], idem_key(state, n, "fleet", 0))
        out({"issue": n, "from": cur, "to": "merged", **result,
             "fleet_card": card})
    verdict = "PASS" if args.pass_ else "FAIL"
    comment = f"## {role.upper()} — ROUND {N} — {verdict}\n\n{detail}\n"
    if args.fail and findings:
        comment += f"\n{findings}\n"
    elif args.pass_ and args.findings_text:
        comment += f"\n{args.findings_text.strip()}\n"
    pr = state.get("pr")
    if pr:
        post_pr_comment(inst, pr, comment)
    elif args.fail:
        post_issue_comment(inst, n, comment)
    card = None
    if not target.startswith("parked-"):
        trole, tnum = parse_label(target)
        card = kanban_create(inst, trole, n, target, state["skill"],
                             state["worktree"], idem_key(state, n, trole, tnum))
        state["cards"].setdefault(trole, []).append(card["id"])
    note = (f"### {role} round {N} — {verdict} -> {target}\n\n{detail}\n"
            + (f" Next card: `{card['id']}` ({card['assignee']})\n" if card
               else " PARKED — owner decides (resume / abandon).\n"))
    edit_issue(inst, n, render_body(issue_body(inst, n), state, note=note),
               add_label=target if not target.startswith("parked-") else target,
               remove_labels=[cur])
    out({"issue": n, "from": cur, "to": target, "state": state, "card": card})


def verb_merge(inst: dict, args) -> None:
    n = args.issue
    state = parse_state(issue_body(inst, n))
    pr = args.pr or state.get("pr")
    if not pr:
        fail("merge requires a PR (state has none; pass --pr URL)")
    result = do_merge(inst, n, state, pr)
    if result.get("status") == "parked":
        out(result)
    card = kanban_create(inst, "fleet", n, "commit-ready", state["skill"],
                         inst["REPO_DIR"], idem_key(state, n, "fleet", 0))
    out({"issue": n, **result, "fleet_card": card})


def verb_resume(inst: dict, args) -> None:
    n = args.issue
    cur = current_state_label(inst, n)
    ready = all_state_labels() - set(PARK_LABELS)
    if args.label not in ready:
        fail(f"--label must be a ready label, got: {args.label}")
    if not cur.startswith("parked-"):
        fail(f"issue {n} is not parked (current: {cur}); resume is for parked issues")
    state = parse_state(issue_body(inst, n))
    role, N = parse_label(args.label)
    # the label IS the round: sync the matching counter to it
    if role in ("author", "audit"):
        state["author_round"] = N
    elif role == "ste100":
        state["ste100_round"] = N
    elif role in ("scripter", "verifier"):
        state["scripter_round"] = N
    note = f"### OWNER RESUME — {cur} -> {args.label}\n"
    if args.note:
        post_issue_comment(inst, n, f"OWNER RESUME: {args.note.strip()}")
        note += f"\n{args.note.strip()}\n"
    trole, tnum = parse_label(args.label)
    card = kanban_create(inst, trole, n, args.label, state["skill"],
                         state["worktree"],
                         idem_key(state, n, trole, tnum) + "-resume")
    state["cards"].setdefault(trole, []).append(card["id"])
    edit_issue(inst, n, render_body(issue_body(inst, n), state, note=note),
               add_label=args.label, remove_labels=[cur])
    out({"issue": n, "from": cur, "to": args.label, "card": card})


def verb_comment(inst: dict, args) -> None:
    n = args.issue
    text = open(args.file).read().strip() if args.file else (args.text or "").strip()
    if not text:
        fail("empty comment (pass --text or --file)")
    state = None
    try:
        state = parse_state(issue_body(inst, n))
    except SystemExit:
        pass
    if state and state.get("pr") and not args.issue_only:
        post_pr_comment(inst, state["pr"], text)
        out({"issue": n, "posted_to": "pr", "pr": state["pr"]})
    else:
        post_issue_comment(inst, n, text)
        out({"issue": n, "posted_to": "issue"})


def verb_status(inst: dict, args) -> None:
    n = args.issue
    body = issue_body(inst, n)
    state = parse_state(body)
    out({"issue": n, "label": current_state_label(inst, n), "state": state,
         "worktree": state.get("worktree"),
         "worktree_exists": os.path.isdir(state.get("worktree", "")),
         "pr": state.get("pr"), "labels": issue_labels(inst, n)})


def verb_list(inst: dict, args) -> None:
    open_issues = gh_json(inst, ["issue", "list", "--label", "pipeline",
                                 "--state", "open", "--limit", "200",
                                 "--json", "number,title,labels"])
    rows = []
    for issue in open_issues:
        names = [l["name"] for l in issue["labels"]]
        try:
            state = parse_state(issue_body(inst, issue["number"]))
        except SystemExit:
            state = None
        rows.append({"issue": issue["number"], "title": issue["title"],
                     "label": next((l for l in names if l in all_state_labels()),
                                   None),
                     "pr": state.get("pr") if state else None,
                     "worktree": state.get("worktree") if state else None})
    out({"open": len(rows), "pipelines": rows})


def verb_abandon(inst: dict, args) -> None:
    if not args.yes:
        fail("abandon is destructive (closes issue, removes worktree + "
             "branch); pass --yes")
    n = args.issue
    cur = current_state_label(inst, n)
    state = parse_state(issue_body(inst, n))
    reason = args.reason or "abandoned by owner"
    post_issue_comment(inst, n, f"### ABANDONED\n\n{reason}\n")
    gh(inst, ["issue", "close", str(n)])
    if os.path.isdir(state.get("worktree", "")):
        git(inst, ["worktree", "remove", state["worktree"], "--force"],
            check=False)
    git(inst, ["branch", "-D", state["branch"]], check=False)
    out({"issue": n, "was": cur, "closed": True,
         "worktree_removed": not os.path.isdir(state.get("worktree", "")),
         "branch_removed": True})


# ---------------------------------------------------------------- main


def guard_main() -> None:
    parser = argparse.ArgumentParser(description="skillpipe state machine")
    parser.add_argument("--instance", default=None,
                        help="instance file (KEY=VALUE); default "
                             "$SKILLPIPE_INSTANCE")
    sub = parser.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("intake", help="open issue + worktree + author card")
    p.add_argument("--skill", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--mode", default="auto", choices=["auto", "create", "update"])
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("intake-all", help="intake every skill (or --skills a,b)")
    p.add_argument("--skills", default=None)
    p.add_argument("--request", required=True)
    p.add_argument("--mode", default="auto", choices=["auto", "create", "update"])

    p = sub.add_parser("transition", help="a role finished its stage")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--role", default=None,
                   help="cross-check: must match the issue's state label")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--pass", dest="pass_", action="store_true")
    group.add_argument("--fail", action="store_true")
    p.add_argument("--pr", default=None,
                   help="PR URL (required for author/scripter PASS)")
    p.add_argument("--findings-file", default=None)
    p.add_argument("--findings-text", default=None)

    p = sub.add_parser("merge", help="merge the PR (commit role, or retry after park)")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--pr", default=None)

    p = sub.add_parser("resume", help="owner: parked -> any ready label")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--note", default=None)

    p = sub.add_parser("comment", help="post a note to the PR (or issue)")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--text", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--issue-only", action="store_true")

    p = sub.add_parser("status", help="one issue's state")
    p.add_argument("--issue", type=int, required=True)

    sub.add_parser("list", help="all open pipelines")

    p = sub.add_parser("abandon", help="close issue + remove worktree/branch")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--reason", default=None)
    p.add_argument("--yes", action="store_true")

    args = parser.parse_args()
    inst = parse_instance(args.instance or os.environ.get("SKILLPIPE_INSTANCE"))
    inst["INSTANCE_PATH"] = os.path.abspath(
        args.instance or os.environ.get("SKILLPIPE_INSTANCE"))
    handlers = {"intake": verb_intake, "intake-all": verb_intake_all,
                "transition": verb_transition, "merge": verb_merge,
                "resume": verb_resume, "comment": verb_comment,
                "status": verb_status, "list": verb_list,
                "abandon": verb_abandon}
    handlers[args.verb](inst, args)


def main() -> None:
    try:
        guard_main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — contract: always one JSON object
        print(json.dumps({"ok": False, "error": f"unhandled: {exc!r}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
