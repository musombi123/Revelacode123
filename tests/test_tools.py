import base64
import json
import os
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from vorax import tools


class ToolLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_root = tools.ARTIFACT_ROOT
        tools.ARTIFACT_ROOT = self.root

    def tearDown(self):
        tools.ARTIFACT_ROOT = self.old_root
        self.temp.cleanup()

    def test_registry_has_stable_replaceable_contracts(self):
        names = {item["name"] for item in tools.registry()}
        self.assertEqual(names, {"image.generate", "audio.tts", "audio.transcribe",
                                 "video.generate", "music.generate", "translate", "code.execute"})
        self.assertTrue(all(item["replaceable"] for item in tools.registry()))

    def test_rejects_unknown_tool_and_arbitrary_provider(self):
        with self.assertRaises(tools.ToolError):
            tools.execute_tool("shell.exec", {})
        with self.assertRaises(tools.ToolError):
            tools.execute_tool("image.generate", {"prompt": "x", "width": 512, "height": 512}, provider="custom-url")

    def test_code_tool_uses_restricted_interpreter_and_writes_artifact(self):
        result = tools.execute_tool("code.execute", {"source": "(8 * 7) + 1", "tests": []})
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.output, {"result": 57})
        self.assertTrue((self.root / result.artifact).is_file())
        with self.assertRaises(tools.ToolError):
            tools.execute_tool("code.execute", {"source": "__import__('os').system('id')", "tests": []})

    def test_image_provider_result_must_be_real_valid_png(self):
        import struct
        # Minimal PNG structure with IHDR, IDAT and IEND chunks.
        def chunk(kind, payload):
            import zlib
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
        raw = (b"\x89PNG\r\n\x1a\n" +
               chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress(b"".join(
                   bytes([0]) + bytes(((x + y) % 256 for x in range(64 * 3)))
                   for y in range(64)
               ))) + chunk(b"IEND", b""))
        with patch.dict(os.environ, {"HF_TOKEN": "test-token"}), patch.object(tools, "_hf", return_value=(raw, "image/png")):
            result = tools.execute_tool("image.generate", {"prompt": "test", "width": 64, "height": 64})
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.verification["format"], "png")
        self.assertGreater(result.size_bytes, 0)


    def test_invalid_png_fails_closed(self):
        with patch.dict(os.environ, {"HF_TOKEN": "test-token"}), patch.object(tools, "_hf", return_value=(b"not an image", "image/png")):
            with self.assertRaises(tools.ToolError):
                tools.execute_tool("image.generate", {"prompt": "test", "width": 64, "height": 64})

    def test_tool_dict_exposes_explicit_verified_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            tools.ARTIFACT_ROOT = Path(folder)
            result = tools.execute_tool_dict("code.execute", {"source": "8 * 7", "tests": []})
            self.assertEqual(result["status"], "verified")
            self.assertIs(result["verified"], True)

    def test_code_tool_does_not_silently_ignore_nonempty_tests(self):
        with self.assertRaises(tools.ToolError):
            tools.execute_tool("code.execute", {"source": "8 * 7", "tests": [{"expect": 56}]})

    def test_missing_provider_credentials_returns_unknown(self):
        with patch.dict(os.environ, {}, clear=True):
            result = tools.execute_tool_dict("audio.tts", {"text": "hello"})
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["verified"])


if __name__ == "__main__":
    unittest.main()
