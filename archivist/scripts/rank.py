#!/usr/bin/env python3
"""
rank.py — read INDEX.md, score entries, print formatted weekly digest.

Usage:
    python3 rank.py /path/to/INDEX.md

Outputs the formatted digest to stdout. Designed to be invoked by:
- post-digest.sh under cron (the webhook poster), or
- the user manually for ad-hoc digest production.

Self-contained beyond Python 3.10+ (no third-party deps).
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

FIELD_RE = re.compile(r"^- \*\*(\w+)\*\*:\s*(.*)$")


@dataclass
class Entry:
    title: str
    url: str = ""
    type: str = ""
    tags: list[str] = field(default_factory=list)
    added: date | None = None
    summary: str = ""
    note: str = ""

    @property
    def days_old(self) -> int:
        if self.added is None:
            return 9999
        return (date.today() - self.added).days


def parse_index(path: Path) -> list[Entry]:
    text = path.read_text(encoding="utf-8")
    entries: list[Entry] = []
    parts = re.split(r"\n(?=### )", text)
    for part in parts:
        if not part.startswith("### "):
            continue
        lines = part.splitlines()
        title = lines[0][4:].strip()
        e = Entry(title=title)
        for line in lines[1:]:
            m = FIELD_RE.match(line)
            if not m:
                continue
            key, value = m.group(1).lower(), m.group(2).strip()
            if key == "url":
                e.url = value
            elif key == "type":
                e.type = value
            elif key == "tags":
                e.tags = [t.strip() for t in value.split() if t.startswith("#")]
            elif key == "added":
                try:
                    e.added = datetime.strptime(value, "%Y-%m-%d").date()
                except ValueError:
                    pass
            elif key == "summary":
                e.summary = value
            elif key == "note":
                e.note = value
        if e.added is not None:
            entries.append(e)
    return entries


def score_entry(
    e: Entry, tag_counts_30d: Counter, max_tag_count: int
) -> tuple[float, str]:
    days_old = e.days_old
    recency = max(0, 30 - days_old) / 30
    forgotten = 0.6 if 30 <= days_old <= 180 else 0.0
    note_bonus = 0.3 if e.note else 0.0

    if max_tag_count > 0 and e.tags:
        tag_pop = sum(tag_counts_30d[t] for t in e.tags) / max_tag_count
        tag_pop = min(tag_pop, 1.0)
    else:
        tag_pop = 0.0

    total = recency + forgotten + tag_pop * 0.5 + note_bonus

    if recency > 0.7:
        reason = "fresh this week"
    elif forgotten > 0:
        weeks = round(days_old / 7)
        reason = f"you saved this {weeks} weeks ago and might want to actually act on it"
    elif tag_pop > 0:
        reason = "matches your most-saved tags lately"
    else:
        reason = "from your archive"

    return total, reason


def pick_top_3_with_variety(
    scored: list[tuple[Entry, float, str]],
) -> list[tuple[Entry, float, str]]:
    if not scored:
        return []
    scored_sorted = sorted(scored, key=lambda x: x[1], reverse=True)
    chosen = [scored_sorted[0]]
    for cand in scored_sorted[1:]:
        if len(chosen) >= 3:
            break
        types_so_far = {c[0].type for c in chosen}
        if cand[0].type in types_so_far:
            replacement = next(
                (
                    s
                    for s in scored_sorted
                    if s not in chosen
                    and s[0].type not in types_so_far
                    and s[1] >= cand[1] - 0.1
                ),
                None,
            )
            if replacement is not None:
                chosen.append(replacement)
                continue
        chosen.append(cand)
    return chosen[:3]


def format_digest(entries: list[Entry]) -> str:
    if not entries:
        return "Archive is empty — nothing to digest yet."

    today_str = date.today().strftime("%Y-%m-%d")

    tag_counter_30d: Counter = Counter()
    for e in entries:
        if e.days_old <= 30:
            tag_counter_30d.update(e.tags)
    max_tag_count = max(tag_counter_30d.values()) if tag_counter_30d else 0

    scored = [(e, *score_entry(e, tag_counter_30d, max_tag_count)) for e in entries]
    top3 = pick_top_3_with_variety(scored)

    tag_counter_14d: Counter = Counter()
    for e in entries:
        if e.days_old <= 14:
            tag_counter_14d.update(e.tags)
    trending = sorted(tag_counter_14d.most_common(), key=lambda x: (-x[1], x[0]))[:3]

    out = [f"**Archivist weekly digest — {today_str}**", "", "Top 3 to revisit:"]
    for i, (e, _score, reason) in enumerate(top3, 1):
        first_sentence = e.summary.split(". ")[0].rstrip(".") + "."
        tags_str = " ".join(e.tags)
        out.append(f"{i}. **{e.title}** — {e.type} · {tags_str}")
        out.append(f"   {first_sentence}")
        out.append(f"   why now: {reason}")
        out.append(f"   {e.url}")

    if trending:
        out.append("")
        out.append("Trending in your archive (last 14 days):")
        out.append(" · ".join(f"{t} ({c})" for t, c in trending))

    return "\n".join(out)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-INDEX.md>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Archive is empty — INDEX.md not found at {path}.")
        sys.exit(0)
    entries = parse_index(path)
    print(format_digest(entries))


if __name__ == "__main__":
    main()
