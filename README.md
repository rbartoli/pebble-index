# pebble-index

Glue for the Pebble Index 01 ring and the Obsidian vault.

## index_to_daily.py

The Pebble app's Obsidian integration writes one file per capture into
`Voice notes/YYYY-MM-DD HHmm.md` (frontmatter `created:` + `tags: [index]`). It has no
"append to today's daily note" mode (its three modes are timestamped files, one main note,
one fixed named note), so this script does it on the M720q, where the vault is Resilio-synced:

1. picks up capture files older than 90 s (settled) tagged `index`
2. appends the transcript as a plain paragraph to `Daily notes/<created date>.md`, blank line
   before it; creates the note with the Templater stub frontmatter if missing
3. moves the capture into the vault's `.trash/` (Obsidian local trash, recoverable)

It skips a daily note edited in the last 60 s (someone is probably typing on another peer),
writes atomically, and keeps a state file so a failed move never double-appends.
Stdlib only, Python 3.10+.

```sh
python3 index_to_daily.py --dry-run          # show what would happen
python3 index_to_daily.py                    # VAULT defaults to ~/Sync/Obsidian/RB
python3 -m unittest discover -s tests -v     # tests
```

### Install on the M720q

```sh
cp ops/index-to-daily.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now index-to-daily.timer
journalctl --user -u index-to-daily -f
```

State: `~/.local/state/pebble-index/processed.json`.
