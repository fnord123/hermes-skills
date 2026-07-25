#!/usr/bin/env python3
"""
news-dedup.py — Select one news story per topic, preferring ranked sources.

Reads a JSON array of {url, title, topic} objects from stdin.
Filters already-seen URLs, then for each topic picks the best story:
preferred sources (ranked by prefs order) first, non-preferred as fallback
when no preferred source is available.

Usage:
  cat stories.json | python3 news-dedup.py [options]

Options:
  --seen-file PATH       Path to seen-URLs JSON file (default: news-seen.json)
  --prefs-file PATH      Path to source preferences JSON file
  --debug                Show per-topic story selection and open source-prefs TUI
  --seen-max INT         Max URLs to keep in seen file (default: 500)
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.parse

SEEN_MAX = 500


def get_domain(url):
    try:
        netloc = urllib.parse.urlparse(url).netloc
        return netloc.lstrip("www.")
    except Exception:
        return ""


def load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def update_seen_file(path, new_urls, max_entries):
    existing = load_json_file(path, [])
    combined = existing + new_urls
    trimmed = combined[-max_entries:]
    save_json_file(path, trimmed)


def pick_best_for_topic(candidates, trusted, untrusted):
    """
    Selection rules (per design D1):
      1. Drop any candidate whose domain is in `untrusted`.
      2. If any survivor's domain is in `trusted`, pick the one with the
         lowest index in `trusted` (= highest priority).
      3. Otherwise pick the first remaining candidate (Brave relevance order).
      4. If everything was untrusted, return None — caller should skip the topic.
    """
    eligible = [s for s in candidates if get_domain(s["url"]) not in untrusted]
    if not eligible:
        return None

    best = None
    best_rank = len(trusted)
    for s in eligible:
        domain = get_domain(s["url"])
        if domain in trusted:
            rank = trusted.index(domain)
            if rank < best_rank:
                best = s
                best_rank = rank
    if best is not None:
        return best

    return eligible[0]


def print_topic_debug(topics, by_topic, picked_map, seen_urls, topic_dups, trusted, untrusted):
    """Print per-topic story candidates with selection markers."""
    print("\n=== Topic Story Selection ===\n", file=sys.stderr)
    for topic in topics:
        print(f"Topic: {topic}", file=sys.stderr)
        candidates = by_topic.get(topic, [])
        if not candidates:
            print("  (no stories returned)", file=sys.stderr)
            print(file=sys.stderr)
            continue

        picked = picked_map.get(topic)
        for s in candidates:
            url = s.get("url", "")
            domain = get_domain(url)
            if domain in trusted:
                rank_str = f"trusted #{trusted.index(domain)+1}"
            elif domain in untrusted:
                rank_str = "untrusted"
            else:
                rank_str = "unlisted"
            if url in seen_urls:
                marker = "[seen]    "
            elif url in topic_dups.get(topic, set()):
                marker = "[dup]     "
            elif domain in untrusted:
                marker = "[BLOCKED] "
            elif picked and url == picked["url"]:
                marker = "[PICK]    "
            else:
                marker = "[skip]    "
            print(f"  {marker} {domain} ({rank_str}) — {s.get('title', '')}", file=sys.stderr)
            print(f"           {url}", file=sys.stderr)

        if picked is None:
            print("  → No story selected (all untrusted, all seen, or empty)", file=sys.stderr)
        print(file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Select one news story per topic")
    parser.add_argument("--seen-file", default="news-seen.json")
    parser.add_argument("--prefs-file", default="news-source-prefs.json")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--seen-max", type=int, default=SEEN_MAX)
    args = parser.parse_args()

    # Read stories from stdin
    try:
        stories = json.load(sys.stdin)
    except Exception as e:
        print(f"Error reading stories from stdin: {e}", file=sys.stderr)
        sys.exit(1)

    if not stories:
        sys.exit(0)

    # Load state
    seen_urls = set(load_json_file(args.seen_file, []))
    prefs_data = load_json_file(args.prefs_file, {})
    trusted = prefs_data.get("trusted", [])
    untrusted = set(prefs_data.get("untrusted", []))

    # Preserve topic order from input; group stories by topic
    topics = []
    by_topic = {}
    for s in stories:
        t = s.get("topic", "")
        if not t:
            continue
        if t not in by_topic:
            topics.append(t)
            by_topic[t] = []
        by_topic[t].append(s)

    # For each topic pick one story, tracking cross-topic URL dups as we go
    picked_this_run = set()
    picked_map = {}
    topic_dups = {}  # topic -> set of URLs excluded because another topic already picked them

    for topic in topics:
        dups = set()
        candidates = []
        for s in by_topic[topic]:
            url = s.get("url", "")
            if not url or url in seen_urls:
                continue
            if url in picked_this_run:
                dups.add(url)
            else:
                candidates.append(s)
        topic_dups[topic] = dups

        if not candidates:
            continue
        picked = pick_best_for_topic(candidates, trusted, untrusted)
        if picked is None:
            continue  # all untrusted — skip this topic
        picked_map[topic] = picked
        picked_this_run.add(picked["url"])

    # Debug: show per-topic selection, then launch source-prefs TUI
    if args.debug:
        print_topic_debug(topics, by_topic, picked_map, seen_urls, topic_dups, trusted, untrusted)

        all_domains = sorted(set(
            get_domain(s["url"])
            for t in topics for s in by_topic.get(t, [])
            if s.get("url")
        ))
        script_dir = os.path.dirname(os.path.abspath(__file__))
        source_prefs_script = os.path.join(script_dir, "source-prefs.py")
        subprocess.run(
            [sys.executable, source_prefs_script,
             "--prefs-file", args.prefs_file,
             "--add-domains", ",".join(all_domains)],
            check=False,
        )
        # Reload prefs after TUI (note: picks were already computed above)
        prefs_data = load_json_file(args.prefs_file, {})
        trusted = prefs_data.get("trusted", [])
        untrusted = set(prefs_data.get("untrusted", []))

    # Output bullet list — one story per topic, in topic order.
    # Use markdown masked-link syntax so Discord renders the headline
    # as a clickable link instead of showing the bare URL.
    for topic in topics:
        if topic in picked_map:
            story = picked_map[topic]
            print(f"- [{story['title']}]({story['url']})")

    # Update seen file with all new URLs from this run (not just picked ones,
    # so non-picked stories don't resurface on the next run)
    new_urls = [
        s["url"] for s in stories
        if s.get("url") and s["url"] not in seen_urls
    ]
    if new_urls:
        update_seen_file(args.seen_file, new_urls, args.seen_max)


if __name__ == "__main__":
    main()
