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
    def test_delivers_to_telegram(self):
        p = build_agent_payload("  hello ", "double-click-hold", {"TELEGRAM_TO": "123"})
        self.assertEqual((p["channel"], p["to"], p["deliver"], p["agentId"]), ("telegram", "123", True, "main"))
        self.assertTrue(p["message"].endswith("hello"))
        self.assertNotIn("test event", p["message"])

    def test_test_event_is_marked(self):
        p = build_agent_payload("x", "test-event", {"TELEGRAM_TO": "1"})
        self.assertTrue(p["message"].startswith("[Pebble test event]"))


if __name__ == "__main__":
    unittest.main()
