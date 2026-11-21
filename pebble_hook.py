#!/usr/bin/env python3
"""Bridge Pebble Index 01 ring webhooks to OpenClaw or Hermes Agent.

The Pebble app sends multipart/form-data with the gesture in X-Index-Trigger.
This server authenticates the sender, selects double-click-hold (and test-event),
and forwards a JSON message to the configured gateway over the tailnet.

Env (secrets in ~/.config/pebble-index/hook.env):
  PEBBLE_HOOK_TOKEN     bearer the Pebble app sends (required)
  PEBBLE_HOOK_BACKEND   openclaw (default) or hermes
  PEBBLE_HOOK_SECRET    optional Pebble request-signing secret
  PEBBLE_HOOK_BIND      default 127.0.0.1:8444

OpenClaw backend:
  OPENCLAW_HOOK_TOKEN   OpenClaw hooks.token (required)
  OPENCLAW_HOOK_URL     default http://127.0.0.1:18789/hooks/ring

Hermes backend:
  HERMES_HOOK_SECRET   Hermes webhook route secret (required)
  HERMES_HOOK_URL      default http://127.0.0.1:8644/webhooks/ring

Agent and delivery routing stay in the gateway configuration. Hermes requests
use timestamp-bound HMAC-SHA256 V2 signatures; see docs/hermes.md for setup.
Stdlib only, Python 3.11+ (multipart is parsed with email, not cgi).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FORWARDED_TRIGGERS = {"double-click-hold", "test-event"}
MAX_BODY = 8 * 1024 * 1024  # audio would be bigger; we only ask for transcription
AGENT_TIMEOUT = 20  # OpenClaw admission can wait up to 15 s
SIGNATURE_WINDOW = 300  # seconds; the window the Pebble webhook spec recommends

_seen_lock = threading.Lock()
_seen_deliveries: dict[str, float] = {}  # delivery ID -> time accepted


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


def check_signature(headers, body: bytes, secret: bytes, seen: dict[str, float], now: float) -> str | None:
    """Verify the app's HMAC-SHA256 request signature (INDEX_WEBHOOK_API.md, "Signed requests").

    Returns None for an authentic, fresh, first-seen request, else the reason to refuse it.
    `seen` maps accepted delivery IDs to when they were accepted, so replays are refused.
    """
    if not headers.get("X-Index-Signature"):
        return "unsigned request"
    version = headers.get("X-Index-Webhook-Version", "")
    if version != "1":
        return "unsupported webhook version"
    ts_text = headers.get("X-Index-Timestamp", "")
    try:
        ts = int(ts_text)
    except ValueError:
        return "bad timestamp"
    if abs(now - ts) > SIGNATURE_WINDOW:
        return "stale timestamp"
    delivery = headers.get("X-Index-Delivery", "")
    trigger = headers.get("X-Index-Trigger", "")
    is_test = headers.get("X-Index-Test") == "true"
    signed = f"v{version}\n{ts_text}\n{delivery}\n{trigger}\n{1 if is_test else 0}\n".encode() + body
    expected = hmac.new(secret, signed, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(expected, headers.get("X-Index-Signature", "").encode("utf-8", "replace")):
        return "bad signature"
    for d, t in list(seen.items()):
        if now - t > SIGNATURE_WINDOW:
            del seen[d]
    if delivery in seen:
        return "replayed delivery"
    seen[delivery] = now
    return None


def build_agent_payload(transcription: str, trigger: str, backend: str = "openclaw") -> dict:
    prefix = "[Pebble test event] " if trigger == "test-event" else ""
    if backend == "hermes":
        return {"message": f"{prefix}Voice message from the Pebble Index ring:\n\n{transcription.strip()}"}
    # ~/.openclaw/bin/recent-replies.py finds ring messages by "from the Pebble Index ring"
    # and skips the test prefix, so the daily question can count spoken answers.
    return {
        "message": (
            f"{prefix}Voice message from the Pebble Index ring (double-click-hold): a question, "
            "or an answer to today's daily question. Reply briefly, as a Telegram message:\n\n"
            + transcription.strip()
        ),
    }


def forward(payload: dict, idempotency_key: str, env: dict[str, str]) -> tuple[int, str]:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    backend = env.get("PEBBLE_HOOK_BACKEND", "openclaw")
    if backend == "hermes":
        url = env.get("HERMES_HOOK_URL", "http://127.0.0.1:8644/webhooks/ring")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            env["HERMES_HOOK_SECRET"].encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        headers.update({
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature-V2": signature,
            "X-Request-ID": idempotency_key,
        })
    elif backend == "openclaw":
        url = env.get("OPENCLAW_HOOK_URL", "http://127.0.0.1:18789/hooks/ring")
        headers.update({
            "Authorization": f"Bearer {env['OPENCLAW_HOOK_TOKEN']}",
            "Idempotency-Key": idempotency_key,
        })
    else:
        raise ValueError("PEBBLE_HOOK_BACKEND must be openclaw or hermes")
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
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

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._reply(413, "bad length\n")
            return
        body = self.rfile.read(length)

        secret = self.env.get("PEBBLE_HOOK_SECRET", "")
        if secret:
            with _seen_lock:
                problem = check_signature(self.headers, body, secret.encode(), _seen_deliveries, time.time())
            if problem:
                log(f"401 {problem} from {self.client_address[0]}")
                self._reply(401, "unauthorized\n")
                return

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
        backend = self.env.get("PEBBLE_HOOK_BACKEND", "openclaw")
        code, text = forward(build_agent_payload(transcription, trigger, backend), key, self.env)
        log(f"trigger={trigger} chars={len(transcription)} {backend}={code} {text[:200]!r}")
        if code == 200 or (backend == "hermes" and code == 202):
            self._reply(200, "ok\n")
        else:
            self._reply(502, "agent unavailable\n")


def main() -> int:
    env = dict(os.environ)
    backend = env.get("PEBBLE_HOOK_BACKEND", "openclaw")
    if backend not in {"openclaw", "hermes"}:
        log("PEBBLE_HOOK_BACKEND must be openclaw or hermes")
        return 2
    credential = "HERMES_HOOK_SECRET" if backend == "hermes" else "OPENCLAW_HOOK_TOKEN"
    missing = [k for k in ("PEBBLE_HOOK_TOKEN", credential) if not env.get(k)]
    if missing:
        log(f"missing env: {', '.join(missing)}")
        return 2
    host, _, port = env.get("PEBBLE_HOOK_BIND", "127.0.0.1:8444").rpartition(":")
    Handler.env = env
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    log(f"pebble-hook listening on {host}:{port}, signing {'on' if env.get('PEBBLE_HOOK_SECRET') else 'off'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
