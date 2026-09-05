#!/usr/bin/env python3
"""Append Pebble Index 01 ring captures to the matching Obsidian daily note.

The Pebble app's Obsidian integration writes one file per capture into
`Voice notes/YYYY-MM-DD HHmm.md` with `created:` and `tags: [index]` frontmatter.
It has no "append to today's daily note" mode, so this script (run every minute
by a systemd user timer on the M720q, where the vault is Resilio-synced) does it:

  1. pick up settled capture files tagged `index`
  2. append the transcript as a plain paragraph to `Daily notes/<created date>.md`
     (creating the note with the Templater stub frontmatter if it does not exist)
  3. move the capture into the vault's `.trash/` (Obsidian local trash, recoverable)

Stdlib only. Idempotent via a state file so a failed move never double-appends.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

CAPTURE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{4}( \(\d+\))?\.md$")
CREATED_RE = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})", re.M)
TAGS_RE = re.compile(r"^tags:\s*\[\s*index\b", re.M)
DAILY_STUB = "---\nprocessed: false\nprocessed_date: \n---\n"

SETTLE_SECONDS = 90  # capture file must be this old (Resilio may still be writing)
QUIET_SECONDS = 60  # daily note must be untouched this long (someone may be editing)


def log(msg: str) -> None:
    print(msg, flush=True)


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). Frontmatter is '' when absent."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end == -1:
        return "", text
    fm = text[4:end]
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    return fm, body


def capture_date(name: str, frontmatter: str) -> str:
    m = CREATED_RE.search(frontmatter)
    if m:
        return m.group(1)
    return CAPTURE_RE.match(name).group(1)


def is_capture(path: Path) -> bool:
    if not CAPTURE_RE.match(path.name):
        return False
    try:
        head = path.read_text(encoding="utf-8")[:400]
    except OSError:
        return False
    fm, _ = split_frontmatter(head)
    return bool(TAGS_RE.search(fm))


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp-index")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def appended(existing: str | None, paragraph: str) -> str:
    """Daily-note text after appending one paragraph with a blank line before it."""
    base = DAILY_STUB if existing is None else existing
    base = base.rstrip("\n") + "\n"
    return f"{base}\n{paragraph}\n"


def trash_target(trash: Path, name: str) -> Path:
    target = trash / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while target.exists():
        target = trash / f"{stem} (trash {n}){suffix}"
        n += 1
    return target


class State:
    def __init__(self, path: Path):
        self.path = path
        self.done: set[str] = set()
        if path.exists():
            try:
                self.done = set(json.loads(path.read_text()))
            except (OSError, ValueError):
                self.done = set()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(sorted(self.done), indent=0))


def process(vault: Path, state: State, *, dry_run: bool = False, now: float | None = None) -> int:
    now = time.time() if now is None else now
    voice = vault / "Voice notes"
    daily_dir = vault / "Daily notes"
    trash = vault / ".trash"
    handled = 0
    touched: set[Path] = set()  # daily notes this run wrote; exempt from the quiet guard

    for path in sorted(voice.glob("*.md")):
        if not is_capture(path):
            continue
        age = now - path.stat().st_mtime
        if age < SETTLE_SECONDS:
            log(f"wait  {path.name}: {age:.0f}s old, letting it settle")
            continue

        text = path.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        day = capture_date(path.name, fm)
        paragraph = body.strip()
        key = f"{path.name}@{day}"
        daily = daily_dir / f"{day}.md"

        if key in state.done:
            log(f"trash {path.name}: already appended earlier, moving only")
        elif not paragraph:
            log(f"empty {path.name}: no transcript, trashing")
        else:
            if daily not in touched and daily.exists() and now - daily.stat().st_mtime < QUIET_SECONDS:
                log(f"wait  {path.name}: {daily.name} edited <{QUIET_SECONDS}s ago")
                continue
            existing = daily.read_text(encoding="utf-8") if daily.exists() else None
            verb = "append" if existing is not None else "create"
            log(f"{verb} {daily.name} <- {path.name}: {paragraph[:60]!r}")
            if not dry_run:
                daily_dir.mkdir(exist_ok=True)
                atomic_write(daily, appended(existing, paragraph))
                touched.add(daily)
                state.done.add(key)
                state.save()

        if not dry_run:
            trash.mkdir(exist_ok=True)
            os.replace(path, trash_target(trash, path.name))
            state.done.discard(key)
            state.save()
        handled += 1

    return handled


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--vault",
        default=os.environ.get("VAULT", "~/Sync/Obsidian/RB"),
        help="vault root (default $VAULT or ~/Sync/Obsidian/RB)",
    )
    ap.add_argument(
        "--state",
        default=os.environ.get(
            "XDG_STATE_HOME", os.path.expanduser("~/.local/state")
        )
        + "/pebble-index/processed.json",
    )
    ap.add_argument("--dry-run", action="store_true", help="log what would happen, change nothing")
    args = ap.parse_args(argv)

    vault = Path(os.path.expanduser(args.vault))
    if not (vault / "Voice notes").is_dir():
        log(f"error: {vault / 'Voice notes'} not found")
        return 2
    n = process(vault, State(Path(args.state)), dry_run=args.dry_run)
    if n:
        log(f"done: {n} capture(s){' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
