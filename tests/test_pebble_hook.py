import hashlib
import hmac
import unittest

from pebble_hook import build_agent_payload, check_signature, parse_multipart


def multipart(fields: dict[str, str], boundary: str = "b0undary") -> tuple[str, bytes]:
    parts = b""
    for name, value in fields.items():
        parts += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
        ).encode()
    parts += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", parts


class ParseMultipart(unittest.TestCase):
    def test_text_fields_extracted(self):
        ct, body = multipart({"transcription": "what did I decide about X?", "recordedAt": "1", "client": "ring"})
        self.assertEqual(
            parse_multipart(ct, body),
            {"transcription": "what did I decide about X?", "recordedAt": "1", "client": "ring"},
        )

    def test_file_parts_skipped(self):
        ct = "multipart/form-data; boundary=b"
        body = (
            b"--b\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"r.m4a\"\r\n"
            b"Content-Type: audio/mp4\r\n\r\n\x00\x01\r\n"
            b"--b\r\nContent-Disposition: form-data; name=\"transcription\"\r\n\r\nhi\r\n--b--\r\n"
        )
        self.assertEqual(parse_multipart(ct, body), {"transcription": "hi"})

    def test_non_multipart_is_empty(self):
        self.assertEqual(parse_multipart("application/json", b"{}"), {})


class AgentPayload(unittest.TestCase):
    def test_message_only_routing_is_gateway_side(self):
        p = build_agent_payload("  hello ", "double-click-hold")
        self.assertEqual(set(p), {"message"})
        self.assertTrue(p["message"].endswith("hello"))
        self.assertNotIn("test event", p["message"])

    def test_marker_recent_replies_keys_on(self):
        p = build_agent_payload("hello", "double-click-hold")
        self.assertIn("from the Pebble Index ring", p["message"])

    def test_test_event_is_marked(self):
        p = build_agent_payload("x", "test-event")
        self.assertTrue(p["message"].startswith("[Pebble test event]"))


SECRET = b"s3cret"
NOW = 1_800_000_000


def signed(body: bytes, ts: int, delivery: str = "d1", secret: bytes = SECRET) -> dict[str, str]:
    prefix = f"v1\n{ts}\n{delivery}\ndouble-click-hold\n0\n".encode()
    return {
        "X-Index-Webhook-Version": "1",
        "X-Index-Timestamp": str(ts),
        "X-Index-Delivery": delivery,
        "X-Index-Trigger": "double-click-hold",
        "X-Index-Signature": hmac.new(secret, prefix + body, hashlib.sha256).hexdigest(),
    }


class Signature(unittest.TestCase):
    def test_valid_request_accepted_once(self):
        seen: dict[str, float] = {}
        h = signed(b"body", NOW)
        self.assertIsNone(check_signature(h, b"body", SECRET, seen, NOW))
        self.assertEqual(check_signature(h, b"body", SECRET, seen, NOW + 1), "replayed delivery")

    def test_tampered_body_or_wrong_secret_refused(self):
        h = signed(b"body", NOW)
        self.assertEqual(check_signature(h, b"bodx", SECRET, {}, NOW), "bad signature")
        self.assertEqual(check_signature(h, b"body", b"other", {}, NOW), "bad signature")

    def test_stale_or_malformed_refused(self):
        self.assertEqual(check_signature(signed(b"b", NOW - 301), b"b", SECRET, {}, NOW), "stale timestamp")
        h = signed(b"b", NOW)
        h["X-Index-Timestamp"] = "soon"
        self.assertEqual(check_signature(h, b"b", SECRET, {}, NOW), "bad timestamp")
        h = signed(b"b", NOW)
        h["X-Index-Webhook-Version"] = "2"
        self.assertEqual(check_signature(h, b"b", SECRET, {}, NOW), "unsupported webhook version")

    def test_old_deliveries_expire_from_the_cache(self):
        seen = {"d1": NOW - 301.0}
        self.assertIsNone(check_signature(signed(b"b", NOW), b"b", SECRET, seen, NOW))


if __name__ == "__main__":
    unittest.main()
