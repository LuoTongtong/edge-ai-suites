#
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#

"""
Lightweight unit tests for the smart_classroom_audio_summary OpenClaw skill.

No ASR or LLM models are loaded: Pipeline is fully mocked.
"""

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs for heavy modules that are not installed in the test env
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


def _ensure_stubs() -> None:
    """Create lightweight module stubs so the skill can be imported."""
    for pkg in [
        "pipeline",
        "utils",
        "utils.runtime_config_loader",
        "utils.storage_manager",
        "utils.session_manager",
        "dto",
        "dto.transcription_dto",
        "dto.audiosource",
    ]:
        _make_stub(pkg)

    # AudioSource enum-like stub
    class _AudioSource:
        AUDIO_FILE = "audio_file"
        MICROPHONE = "microphone"

    sys.modules["dto.audiosource"].AudioSource = _AudioSource  # type: ignore[attr-defined]

    # TranscriptionRequest stub
    class _TranscriptionRequest:
        def __init__(self, audio_filename=None, source_type=None):
            self.audio_filename = audio_filename
            self.source_type = source_type

    sys.modules["dto.transcription_dto"].TranscriptionRequest = _TranscriptionRequest  # type: ignore[attr-defined]

    # RuntimeConfig stub
    class _RuntimeConfig:
        @staticmethod
        def get_section(section):
            return {"location": "/tmp/sc_test", "name": "smart-classroom"}

    sys.modules["utils.runtime_config_loader"].RuntimeConfig = _RuntimeConfig  # type: ignore[attr-defined]

    # StorageManager stub
    class _StorageManager:
        @staticmethod
        def read_text_file(path):
            return "TEACHER: hello world"

    sys.modules["utils.storage_manager"].StorageManager = _StorageManager  # type: ignore[attr-defined]

    # session_manager stub
    sys.modules["utils.session_manager"].generate_session_id = lambda: "test-session-001"  # type: ignore[attr-defined]


_ensure_stubs()

# Now we can safely import the skill module
import importlib.util, os, pathlib

_SKILL_PATH = pathlib.Path(__file__).parent.parent / "audio_summary" / "skill.py"
_spec = importlib.util.spec_from_file_location("_audio_summary_skill", _SKILL_PATH)
_skill_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_skill_mod)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAudioSummaryInputValidation(unittest.TestCase):

    def test_missing_audio_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _skill_mod._validate_input({})
        self.assertIn("audio_file", str(ctx.exception))

    def test_invalid_source_type_raises(self):
        # Use audio_filename (no file-existence check) so validation reaches source_type check
        with self.assertRaises(ValueError) as ctx:
            _skill_mod._validate_input({"audio_filename": "lecture.mp3", "source_type": "stream"})
        self.assertIn("source_type", str(ctx.exception))

    def test_audio_file_not_found_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _skill_mod._validate_input({"audio_file": "/nonexistent/file.mp3"})
        self.assertIn("does not exist", str(ctx.exception))

    def test_audio_filename_accepted(self):
        result = _skill_mod._validate_input({"audio_filename": "lecture.mp3"})
        self.assertEqual(result["audio_filename"], "lecture.mp3")
        self.assertIsNone(result["audio_file"])

    def test_defaults_are_applied(self):
        result = _skill_mod._validate_input({"audio_filename": "lecture.mp3"})
        self.assertEqual(result["source_type"], "audio_file")
        self.assertTrue(result["include_transcript"])

    def test_include_transcript_false(self):
        result = _skill_mod._validate_input({"audio_filename": "x.mp3", "include_transcript": False})
        self.assertFalse(result["include_transcript"])


class TestAudioSummaryOutputShaping(unittest.TestCase):
    """Verify that _execute produces the expected output structure when Pipeline is mocked."""

    def _make_mock_pipeline(self, transcription_chunks=None, summary_tokens=None):
        mock_pipeline = MagicMock()
        mock_pipeline.session_id = "test-session-001"
        mock_pipeline.run_transcription.return_value = iter(transcription_chunks or [{"text": "hello"}])
        mock_pipeline.run_summarizer.return_value = iter(summary_tokens or ["## Summary\n", "- point 1"])
        return mock_pipeline

    def test_basic_output_fields(self):
        mock_pipeline = self._make_mock_pipeline()

        params = {
            "audio_file": None,
            "audio_filename": "lecture.mp3",
            "session_id": None,
            "source_type": "audio_file",
            "include_transcript": False,
        }

        with patch.object(_skill_mod, "_execute", wraps=_skill_mod._execute):
            # Patch Pipeline inside the skill's module namespace
            with patch.dict("sys.modules", {"pipeline": MagicMock(Pipeline=MagicMock(return_value=mock_pipeline))}):
                result = _skill_mod._execute(params)

        self.assertIn("session_id", result)
        self.assertIn("summary_markdown", result)
        self.assertIn("source", result)
        self.assertNotIn("transcript", result)

    def test_include_transcript_adds_field(self):
        mock_pipeline = self._make_mock_pipeline()

        params = {
            "audio_file": None,
            "audio_filename": "lecture.mp3",
            "session_id": None,
            "source_type": "audio_file",
            "include_transcript": True,
        }

        with patch.dict("sys.modules", {"pipeline": MagicMock(Pipeline=MagicMock(return_value=mock_pipeline))}):
            result = _skill_mod._execute(params)

        self.assertIn("transcript", result)

    def test_summary_tokens_concatenated(self):
        tokens = ["## Summary\n", "- Wave-particle duality\n", "- Uncertainty principle"]
        mock_pipeline = self._make_mock_pipeline(summary_tokens=tokens)

        params = {
            "audio_file": None,
            "audio_filename": "x.mp3",
            "session_id": None,
            "source_type": "audio_file",
            "include_transcript": False,
        }

        with patch.dict("sys.modules", {"pipeline": MagicMock(Pipeline=MagicMock(return_value=mock_pipeline))}):
            result = _skill_mod._execute(params)

        self.assertEqual(result["summary_markdown"], "".join(tokens))

    def test_source_field_audio_file(self):
        import tempfile, os

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"fake audio")
            tmp_path = f.name

        try:
            mock_pipeline = self._make_mock_pipeline()
            params = {
                "audio_file": tmp_path,
                "audio_filename": None,
                "session_id": None,
                "source_type": "audio_file",
                "include_transcript": False,
            }

            with patch.dict("sys.modules", {"pipeline": MagicMock(Pipeline=MagicMock(return_value=mock_pipeline))}):
                result = _skill_mod._execute(params)

            self.assertEqual(result["source"], "audio_file")
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
