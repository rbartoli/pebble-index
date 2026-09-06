#!/usr/bin/env python3
"""Bridge Pebble Index 01 ring webhooks to OpenClaw's `POST /hooks/agent`.

The Pebble app can POST each recording to a webhook, but as multipart/form-data
(`transcription`, `recordedAt`, `client`, optional `audio`) with the gesture in an
`X-Index-Trigger` header. OpenClaw's hook ingress only accepts JSON. This server
(a systemd user service on the M720q, reached over the tailnet via
`tailscale serve --https=8444`) sits between them:

  1. checks the app's `Authorization: Bearer` header against PEBBLE_HOOK_TOKEN
  2. keeps only `double-click-hold` recordings (and `test-event`, so the app's
     "Send test event" button proves the whole chain); other gestures get 204
  3. forwards the transcript as an isolated agent turn, delivered to Telegram

Env (see ops/pebble-hook.service, secrets in ~/.config/pebble-index/hook.env):
  PEBBLE_HOOK_TOKEN    bearer the Pebble app sends (required)
  OPENCLAW_HOOK_TOKEN  OpenClaw `hooks.token` (required)
  OPENCLAW_HOOK_URL    default http://127.0.0.1:18789/hooks/agent
  TELEGRAM_TO          Telegram chat id to deliver the reply to (required)
  OPENCLAW_AGENT_ID    default main
  PEBBLE_HOOK_BIND     default 127.0.0.1:8444

Stdlib only, Python 3.11+ (no `cgi`; multipart is parsed with `email`).
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FORWARDED_TRIGGERS = {"double-click-hold", "test-event"}
MAX_BODY = 8 * 1024 * 1024  # audio would be bigger; we only ask for transcription
AGENT_TIMEOUT = 20  # OpenClaw admission can wait up to 15 s


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_multipart(content_type: str, body: bytes) -> dict[str, str]:
    """Return the text fields of a multipart/form-data body (binary parts skipped)."""
    msg = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    fields: dict[str, str] = {}
    if not msg.is_multipart():
        return fields
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        fields[str(name)] = payload.decode("utf-8", "replace")
    return fields


def build_agent_payload(transcription: str, trigger: str, env: dict[str, str]) -> dict:
    prefix = "[Pebble test event] " if trigger == "test-event" else ""
    return {
        "message": (
            f"{prefix}Voice question from the Pebble Index ring (double-click-hold). "
            "Answer briefly, as a Telegram message:\n\n" + transcription.strip()
        ),
        "name": "Pebble Index",
        "agentId": env.get("OPENCLAW_AGENT_ID", "main"),
        "sessionMode": "isolated",
        "deliver": True,
        "channel": "telegram",
        "to": env["TELEGRAM_TO"],
    }


def forward(payload: dict, idempotency_key: str, env: dict[str, str]) -> tuple[int, str]:
    req = urllib.request.Request(
        env.get("OPENCLAW_HOOK_URL", "http://127.0.0.1:18789/hooks/agent"),
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {env['OPENCLAW_HOOK_TOKEN']}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        return 502, str(e)


class Handler(BaseHTTPRequestHandler):
    env: dict[str, str] = {}

    def log_message(self, fmt, *args):  # quiet the default access log
        pass

    def _reply(self, code: int, text: str = "") -> None:
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        self._reply(200, "pebble-hook ok\n")

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.env['PEBBLE_HOOK_TOKEN']}"
        if not hmac.compare_digest(auth.encode(), expected.encode()):
            log(f"401 from {self.client_address[0]}")
            self._reply(401, "unauthorized\n")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._reply(413, "bad length\n")
            return
        body = self.rfile.read(length)

        trigger = self.headers.get("X-Index-Trigger", "")
        if trigger not in FORWARDED_TRIGGERS:
            log(f"ignored trigger={trigger!r}")
            self._reply(204)
            return

        fields = parse_multipart(self.headers.get("Content-Type", ""), body)
        transcription = fields.get("transcription", "").strip()
        if not transcription:
            log(f"400 no transcription, fields={sorted(fields)} (set 'What to send' to Transcription)")
            self._reply(400, "no transcription field\n")
            return

        key = self.headers.get("X-Index-Delivery") or f"pebble-{fields.get('recordedAt', '')}"
        code, text = forward(build_agent_payload(transcription, trigger, self.env), key, self.env)
        log(f"trigger={trigger} chars={len(transcription)} openclaw={code} {text[:200]!r}")
        self._reply(200 if code == 200 else 502, text + "\n")


def main() -> int:
    env = dict(os.environ)
    missing = [k for k in ("PEBBLE_HOOK_TOKEN", "OPENCLAW_HOOK_TOKEN", "TELEGRAM_TO") if not env.get(k)]
    if missing:
        log(f"missing env: {', '.join(missing)}")
        return 2
    host, _, port = env.get("PEBBLE_HOOK_BIND", "127.0.0.1:8444").rpartition(":")
    Handler.env = env
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    log(f"pebble-hook listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
