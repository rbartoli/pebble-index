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

## pebble_hook.py

"Ask the vault from the ring, answer on Telegram." The Pebble app can POST each recording to a
webhook, but as multipart/form-data with the gesture in an `X-Index-Trigger` header
(spec: `experimental/.../indexwebhook/INDEX_WEBHOOK_API.md` in `coredevices/mobileapp`).
OpenClaw's hook ingress only takes JSON, so this small server bridges the two:

1. checks the app's `Authorization: Bearer <PEBBLE_HOOK_TOKEN>` header
2. forwards only `double-click-hold` (and the app's `test-event`); other gestures get 204
3. posts the transcript to the gateway's `ring` mapping (`POST /hooks/ring`), which runs it in
   the Telegram DM session and replies there. The session is persistent, so a follow-up keeps
   its context and a spoken answer to the daily question lands right after the question.

Stdlib only, Python 3.11+ (multipart parsed with `email`, no `cgi`).

### Install on the M720q

```sh
# secrets (mode 600): PEBBLE_HOOK_TOKEN, OPENCLAW_HOOK_TOKEN (= openclaw.json hooks.token)
$EDITOR ~/.config/pebble-index/hook.env
cp ops/pebble-hook.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pebble-hook.service
tailscale serve --bg --https=8444 http://127.0.0.1:8444     # tailnet only, no Funnel
journalctl --user -u pebble-hook -f
```

OpenClaw side (`~/.openclaw/openclaw.json`), hot-reloaded. The session key is static and lives in
the mapping, so callers still can't pick sessions (`allowRequestSessionKey: false`):

```json5
hooks: {
  enabled: true, token, path: "/hooks", allowedAgentIds: ["main"], allowRequestSessionKey: false,
  allowedSessionKeyPrefixes: ["hook:", "agent:main:telegram:direct:<chat id>"],
  mappings: [{
    id: "pebble-ring", match: { path: "ring" }, action: "agent", agentId: "main",
    name: "Pebble Index", messageTemplate: "{{message}}",
    sessionKey: "agent:main:telegram:direct:<chat id>", sessionMode: "persistent",
    deliver: true, channel: "telegram", to: "<chat id>",
  }],
}
```

Pebble app → Index 01 Settings → Webhook → **Double click & hold**: URL
`https://<host>.<tailnet>.ts.net:8444/index`, What to send **Transcription**, header
`Authorization` = `Bearer <PEBBLE_HOOK_TOKEN>`. "Send test event" should produce a Telegram reply.
