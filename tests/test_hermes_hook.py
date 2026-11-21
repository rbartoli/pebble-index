"""Hermes backend contract tests; no running agent or credentials required."""

import hashlib
import hmac
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.error
import urllib.request
from unittest.mock import patch

from pebble_hook import Handler, build_agent_payload, forward, main


class HermesForward(unittest.TestCase):
    def test_signs_exact_body_with_timestamp_and_delivery_id(self):
        received = {}

        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                received["path"] = self.path
                received["headers"] = self.headers
                received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b'{"status":"accepted"}')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {
                "PEBBLE_HOOK_BACKEND": "hermes",
                "HERMES_HOOK_URL": f"http://127.0.0.1:{server.server_port}/webhooks/ring",
                "HERMES_HOOK_SECRET": "test-route-secret",
            }
            payload = {"message": "Hello, café 👋"}
            self.assertEqual(forward(payload, "ring-delivery-1", env)[0], 202)
            self.assertEqual(received["path"], "/webhooks/ring")
            headers = received["headers"]
            self.assertEqual(headers["X-Request-ID"], "ring-delivery-1")
            self.assertIsNone(headers.get("Authorization"))
            timestamp = headers["X-Webhook-Timestamp"]
            self.assertGreater(int(timestamp), 0)
            expected = hmac.new(
                b"test-route-secret", timestamp.encode() + b"." + received["body"], hashlib.sha256
            ).hexdigest()
            self.assertEqual(headers["X-Webhook-Signature-V2"], expected)
            self.assertEqual(json.loads(received["body"]), payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class HermesPayload(unittest.TestCase):
    def test_prompt_is_not_coupled_to_telegram_or_daily_questions(self):
        payload = build_agent_payload("  hello ", "double-click-hold", backend="hermes")
        self.assertEqual(set(payload), {"message"})
        self.assertTrue(payload["message"].endswith("hello"))
        self.assertNotIn("Telegram", payload["message"])
        self.assertNotIn("daily question", payload["message"])


class HermesAdmission(unittest.TestCase):
    def test_accepted_event_returns_success_and_uses_hermes_prompt(self):
        class Bridge(Handler):
            env = {"PEBBLE_HOOK_BACKEND": "hermes", "PEBBLE_HOOK_TOKEN": "test-pebble"}

        server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body = (b'--b\r\nContent-Disposition: form-data; name="transcription"\r\n\r\n'
                b'What is next?\r\n--b--\r\n')
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/index", data=body,
            headers={"Authorization": "Bearer test-pebble", "X-Index-Trigger": "double-click-hold",
                     "X-Index-Delivery": "delivery-202", "Content-Type": "multipart/form-data; boundary=b"},
        )
        try:
            with patch("pebble_hook.forward", return_value=(202, '{"status":"accepted"}')) as send:
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.status, 200)
                payload, key, env = send.call_args.args
                self.assertEqual(key, "delivery-202")
                self.assertNotIn("Telegram", payload["message"])
                self.assertTrue(payload["message"].endswith("What is next?"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class HermesConfiguration(unittest.TestCase):
    def test_missing_hermes_secret_refuses_to_bind(self):
        env = {"PEBBLE_HOOK_BACKEND": "hermes", "PEBBLE_HOOK_TOKEN": "test-pebble"}
        with patch.dict("os.environ", env, clear=True), patch("pebble_hook.ThreadingHTTPServer") as server:
            self.assertEqual(main(), 2)
            server.assert_not_called()

    def test_missing_pebble_token_refuses_to_bind(self):
        env = {"PEBBLE_HOOK_BACKEND": "hermes", "HERMES_HOOK_SECRET": "test-hermes"}
        with patch.dict("os.environ", env, clear=True), patch("pebble_hook.ThreadingHTTPServer") as server:
            self.assertEqual(main(), 2)
            server.assert_not_called()

    def test_unknown_backend_refuses_to_bind(self):
        with patch.dict("os.environ", {"PEBBLE_HOOK_BACKEND": "unknown"}, clear=True):
            with patch("pebble_hook.ThreadingHTTPServer") as server:
                self.assertEqual(main(), 2)
                server.assert_not_called()

    def test_default_backend_still_requires_openclaw_token(self):
        with patch.dict("os.environ", {"PEBBLE_HOOK_TOKEN": "test-pebble"}, clear=True):
            with patch("pebble_hook.ThreadingHTTPServer") as server:
                self.assertEqual(main(), 2)
                server.assert_not_called()

    def test_hermes_starts_without_openclaw_token(self):
        env = {"PEBBLE_HOOK_BACKEND": "hermes", "PEBBLE_HOOK_TOKEN": "test-pebble",
               "HERMES_HOOK_SECRET": "test-hermes"}
        with patch.dict("os.environ", env, clear=True), patch("pebble_hook.ThreadingHTTPServer") as server:
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(main(), 0)
            server.assert_called_once()


if __name__ == "__main__":
    unittest.main()
