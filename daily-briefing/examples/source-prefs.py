#!/usr/bin/env python3
"""
source-prefs.py — Interactive TUI for ordering news source preferences.

Domains above the separator are preferred (in order).
Domains below the separator are ignored when picking stories from clusters.

Usage:
  python3 tools/source-prefs.py [--prefs-file PATH] [--add-domains dom1,dom2,...]

Controls:
  ↑ / ↓       Move cursor
  Enter       Grab item (then ↑/↓ to drag it, Enter to drop)
  q           Save and quit
  Esc         Quit without saving

Options:
  --prefs-file PATH     Path to news-source-prefs.json (default: tools/news-source-prefs.json)
  --add-domains LIST    Comma-separated domains to add (placed below separator if new)
"""

import argparse
import curses
import json
import os
import subprocess
import sys

SEPARATOR = "─── untrusted (move items below to block them) ───"
DEFAULT_PREFS_FILE = os.path.join(os.path.dirname(__file__), "news-source-prefs.json")


def load_prefs(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            trusted = data.get("trusted", data.get("preferred", []))
            untrusted = data.get("untrusted", data.get("non_preferred", data.get("excluded", [])))
            return trusted, untrusted
        except Exception:
            pass
    return [], []


def save_prefs(path, trusted, untrusted):
    with open(path, "w") as f:
        json.dump({"trusted": trusted, "untrusted": untrusted}, f, indent=2)
        f.write("\n")


def build_item_list(preferred, excluded, new_domains):
    """Build ordered list with separator. New domains go below separator."""
    items = list(preferred)
    items.append(SEPARATOR)
    below = list(excluded)
    for d in new_domains:
        if d not in items and d not in below:
            below.append(d)
    items.extend(below)
    return items


def split_at_separator(items):
    sep_idx = next((i for i, x in enumerate(items) if x == SEPARATOR), len(items))
    preferred = [x for x in items[:sep_idx] if x != SEPARATOR]
    excluded = [x for x in items[sep_idx + 1:] if x != SEPARATOR]
    return preferred, excluded


def run_tui(stdscr, items):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)   # cursor
    curses.init_pair(2, curses.COLOR_YELLOW, -1)                  # grabbed
    curses.init_pair(3, curses.COLOR_CYAN, -1)                    # separator
    curses.init_pair(4, curses.COLOR_WHITE, -1)                   # normal

    cursor = 0
    grabbed = False
    scroll = 0  # top visible row index

    while True:
        h, w = stdscr.getmaxyx()
        content_rows = h - 4  # rows available for items (header=2, footer=2)

        # Scroll to keep cursor visible
        if cursor < scroll:
            scroll = cursor
        elif cursor >= scroll + content_rows:
            scroll = cursor - content_rows + 1

        stdscr.erase()

        # Header
        if grabbed:
            hdr = " GRABBED — ↑↓ drag item, Enter to drop, Esc cancel grab"
            stdscr.addstr(0, 0, hdr[:w - 1], curses.color_pair(2) | curses.A_BOLD)
        else:
            hdr = " ↑↓ move  Enter grab  q save & quit  Esc cancel"
            stdscr.addstr(0, 0, hdr[:w - 1], curses.A_DIM)
        stdscr.addstr(1, 0, "─" * min(w - 1, 60))

        # Items
        for row_offset in range(content_rows):
            i = scroll + row_offset
            if i >= len(items):
                break
            y = row_offset + 2
            item = items[i]
            is_sep = item == SEPARATOR
            is_cursor = i == cursor

            if is_cursor and grabbed:
                attr = curses.color_pair(2) | curses.A_BOLD
                prefix = " » "
            elif is_cursor:
                attr = curses.color_pair(1)
                prefix = " ▶ "
            elif is_sep:
                attr = curses.color_pair(3) | curses.A_DIM
                prefix = "   "
            else:
                attr = curses.color_pair(4)
                prefix = "   "

            text = (prefix + item)[:w - 1]
            stdscr.addstr(y, 0, text, attr)

        # Footer
        sep_idx = next((i for i, x in enumerate(items) if x == SEPARATOR), len(items))
        n_pref = sum(1 for x in items[:sep_idx] if x != SEPARATOR)
        n_excl = len(items) - sep_idx - 1
        scroll_info = f"  [{scroll + 1}-{min(scroll + content_rows, len(items))}/{len(items)}]" if len(items) > content_rows else ""
        footer = f" Preferred: {n_pref}   Non-preferred: {n_excl}{scroll_info}"
        stdscr.addstr(h - 1, 0, footer[:w - 1], curses.A_DIM)

        stdscr.refresh()

        key = stdscr.getch()

        if key == ord("q") or key == ord("Q"):
            return items, True

        elif key == 27:  # Esc
            if grabbed:
                grabbed = False
            else:
                return items, False

        elif key == curses.KEY_UP:
            if grabbed:
                if cursor > 0:
                    items[cursor], items[cursor - 1] = items[cursor - 1], items[cursor]
                    cursor -= 1
            else:
                if cursor > 0:
                    cursor -= 1

        elif key == curses.KEY_DOWN:
            if grabbed:
                if cursor < len(items) - 1:
                    items[cursor], items[cursor + 1] = items[cursor + 1], items[cursor]
                    cursor += 1
            else:
                if cursor < len(items) - 1:
                    cursor += 1

        elif key in (curses.KEY_ENTER, 10, 13):
            if items[cursor] != SEPARATOR:
                grabbed = not grabbed

    return items, True


def main():
    parser = argparse.ArgumentParser(
        description="Interactive TUI for ordering news source preferences",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--prefs-file", default=DEFAULT_PREFS_FILE)
    parser.add_argument("--add-domains", default="",
                        help="Comma-separated domains to add below the separator if new")
    args = parser.parse_args()

    preferred, non_preferred = load_prefs(args.prefs_file)
    new_domains = [d.strip().lstrip("www.") for d in args.add_domains.split(",") if d.strip()]

    items = build_item_list(preferred, non_preferred, new_domains)

    # When called from a pipeline (e.g. via news-dedup.py), stdin/stdout may be
    # pipes rather than the terminal. Redirect them to /dev/tty so curses can
    # read keystrokes. Skip the redirect if stdin is already a terminal.
    try:
        if not os.isatty(sys.stdin.fileno()):
            tty_fd = os.open("/dev/tty", os.O_RDWR)
            os.dup2(tty_fd, sys.stdin.fileno())
            os.dup2(tty_fd, sys.stdout.fileno())
            os.close(tty_fd)
    except OSError:
        pass

    result_items, do_save = curses.wrapper(run_tui, items)

    if do_save:
        new_preferred, new_non_preferred = split_at_separator(result_items)
        save_prefs(args.prefs_file, new_preferred, new_non_preferred)
        print(f"Saved: {len(new_preferred)} preferred, {len(new_non_preferred)} non-preferred",
              file=sys.stderr)
    else:
        print("Cancelled — no changes saved.", file=sys.stderr)


if __name__ == "__main__":
    main()
