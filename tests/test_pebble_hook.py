import unittest

from pebble_hook import build_agent_payload, parse_multipart


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


if __name__ == "__main__":
    unittest.main()
