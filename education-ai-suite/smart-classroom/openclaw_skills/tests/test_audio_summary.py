"""
Lightweight unit tests for the OpenClaw audio_summary skill.

These tests rely entirely on mocking and do NOT load any ASR or LLM models.

Run from the smart-classroom directory:
    python -m pytest openclaw_skills/tests/test_audio_summary.py -v
"""

import os
import sys
import types
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Stub out heavy Smart Classroom imports so tests can run without installing
# the full model stack.
# ---------------------------------------------------------------------------

def _make_stub_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _stub_dependencies():
    """Create minimal stubs for modules that are loaded at import time."""
    # utils stubs
    for mod_name in [
        "utils",
        "utils.config_loader",
        "utils.runtime_config_loader",
        "utils.storage_manager",
        "utils.session_manager",
        "utils.locks",
        "utils.logger_config",
        "utils.session_state_manager",
        "utils.content_search_client",
        "utils.media_validation_service",
        "utils.markdown_cleaner",
        "utils.platform_info",
        "utils.audio_util",
        "utils.ov_genai_util",
    ]:
        _make_stub_module(mod_name)

    # dto stubs
    for mod_name in [
        "dto",
        "dto.audiosource",
        "dto.transcription_dto",
        "dto.summarizer_dto",
        "dto.project_settings",
        "dto.video_analytics_dto",
        "dto.video_metadata_dto",
        "dto.search_dto",
        "dto.ocr_dto",
    ]:
        _make_stub_module(mod_name)

    # component stubs
    for mod_name in [
        "components",
        "components.base_component",
        "components.stream_reader",
        "components.asr_component",
        "components.summarizer_component",
        "components.mindmap_component",
        "components.segmentation",
        "components.segmentation.content_segmentation",
        "components.ffmpeg",
        "components.ffmpeg.audio_preprocessing",
        "components.llm",
        "components.llm.ipex",
        "components.llm.ipex.summarizer",
        "components.llm.openvino",
        "components.llm.openvino.summarizer",
        "components.llm.openvino_genai",
        "components.llm.openvino_genai.summarizer",
        "components.va",
        "components.va.va_pipeline_service",
        "components.ocr",
        "components.ocr.ocr_pipeline",
        "components.asr",
        "monitoring",
        "monitoring.monitor",
    ]:
        _make_stub_module(mod_name)

    # pipeline stub (heavy model init)
    _make_stub_module("pipeline")

    # pydantic BaseModel stub
    try:
        import pydantic  # noqa: F401 -- already installed, do nothing
    except ImportError:
        pydantic_mod = _make_stub_module("pydantic")

        class _BaseModel:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        pydantic_mod.BaseModel = _BaseModel

    # AudioSource enum stub
    from enum import Enum

    class AudioSource(Enum):
        AUDIO_FILE = "audio_file"
        MICROPHONE = "microphone"

    sys.modules["dto.audiosource"].AudioSource = AudioSource

    # TranscriptionRequest stub
    class TranscriptionRequest:
        def __init__(self, audio_filename, source_type=None):
            self.audio_filename = audio_filename
            self.source_type = source_type

    sys.modules["dto.transcription_dto"].TranscriptionRequest = TranscriptionRequest

    # RuntimeConfig stub
    class _RuntimeConfig:
        @staticmethod
        def get_section(section):
            return {"location": "/tmp/sc_test_storage", "name": "smart-classroom"}

    sys.modules["utils.runtime_config_loader"].RuntimeConfig = _RuntimeConfig

    # StorageManager stub
    class _StorageManager:
        @staticmethod
        def read_text_file(path):
            return "TEACHER: Hello class."

    sys.modules["utils.storage_manager"].StorageManager = _StorageManager

    # session_manager stub
    sys.modules["utils.session_manager"].generate_session_id = lambda: "20240101-120000-ab12"

    # config stub (needed by some conditional imports)
    cfg = MagicMock()
    cfg.app.use_ov_genai = False
    sys.modules["utils.config_loader"].config = cfg


_stub_dependencies()

# ---------------------------------------------------------------------------
# Now we can safely import the skill module
# ---------------------------------------------------------------------------

# Ensure smart-classroom root is on the path
_SC_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _SC_ROOT not in sys.path:
    sys.path.insert(0, _SC_ROOT)

_SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SKILLS_DIR not in sys.path:
    sys.path.insert(0, _SKILLS_DIR)

from openclaw_skills.audio_summary import (  # noqa: E402
    AudioSummaryInputError,
    AudioSummarySkill,
    _resolve_audio_path,
    run_audio_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_pipeline(transcription_chunks=None, summary_tokens=None):
    """Return a mock Pipeline instance."""
    if transcription_chunks is None:
        transcription_chunks = [
            {"text": "Hello class.", "segments": [], "chunk_path": "/tmp/c0.wav"},
            {"event": "final", "teacher_speaker": "TEACHER", "speaker_text_stats": {}},
        ]
    if summary_tokens is None:
        summary_tokens = ["## Summary\n", "Key point 1.\n"]

    mock_pipeline = MagicMock()
    mock_pipeline.run_transcription = MagicMock(return_value=iter(transcription_chunks))
    mock_pipeline.run_summarizer = MagicMock(return_value=iter(summary_tokens))
    return mock_pipeline


def _make_pipeline_factory(mock_pipeline):
    def factory(session_id):
        mock_pipeline.session_id = session_id
        return mock_pipeline

    return factory


# ---------------------------------------------------------------------------
# Tests: input validation
# ---------------------------------------------------------------------------

class TestResolveAudioPath(unittest.TestCase):
    def test_raises_when_neither_provided(self):
        with self.assertRaises(AudioSummaryInputError) as ctx:
            _resolve_audio_path(None, None)
        self.assertIn("audio_file", str(ctx.exception))

    def test_raises_when_audio_file_not_found(self):
        with self.assertRaises(AudioSummaryInputError) as ctx:
            _resolve_audio_path("/nonexistent/path/audio.wav", None)
        self.assertIn("not found", str(ctx.exception))

    def test_returns_absolute_path_for_existing_file(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            result = _resolve_audio_path(tmp_path, None)
            self.assertEqual(result, os.path.abspath(tmp_path))
        finally:
            os.unlink(tmp_path)

    def test_audio_file_takes_priority_over_audio_filename(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            result = _resolve_audio_path(tmp_path, "some_other_file.wav")
            self.assertEqual(result, os.path.abspath(tmp_path))
        finally:
            os.unlink(tmp_path)


class TestAudioSummarySkillValidation(unittest.TestCase):
    def setUp(self):
        self.skill = AudioSummarySkill(pipeline_factory=lambda sid: _make_mock_pipeline())

    def test_raises_on_missing_audio(self):
        with self.assertRaises(AudioSummaryInputError):
            self.skill.invoke()

    def test_raises_on_invalid_source_type(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            with self.assertRaises(AudioSummaryInputError) as ctx:
                self.skill.invoke(audio_file=tmp_path, source_type="webcam")
            self.assertIn("source_type", str(ctx.exception))
        finally:
            os.unlink(tmp_path)

    def test_raises_on_microphone_source_type(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            with self.assertRaises(AudioSummaryInputError) as ctx:
                self.skill.invoke(audio_file=tmp_path, source_type="microphone")
            self.assertIn("microphone", str(ctx.exception))
        finally:
            os.unlink(tmp_path)

    def test_raises_on_nonexistent_audio_file(self):
        with self.assertRaises(AudioSummaryInputError):
            self.skill.invoke(audio_file="/nonexistent/lecture.wav")


# ---------------------------------------------------------------------------
# Tests: successful invocation with mocked pipeline
# ---------------------------------------------------------------------------

class TestAudioSummarySkillInvoke(unittest.TestCase):
    def _run_with_tmp_audio(self, **kwargs):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            mock_pipeline = _make_mock_pipeline()
            skill = AudioSummarySkill(
                pipeline_factory=_make_pipeline_factory(mock_pipeline)
            )
            return skill.invoke(audio_file=tmp_path, **kwargs), mock_pipeline
        finally:
            os.unlink(tmp_path)

    def test_returns_session_id(self):
        result, _ = self._run_with_tmp_audio()
        self.assertIn("session_id", result)
        self.assertIsInstance(result["session_id"], str)
        self.assertTrue(result["session_id"])

    def test_returns_summary_markdown(self):
        result, _ = self._run_with_tmp_audio()
        self.assertIn("summary_markdown", result)
        self.assertEqual(result["summary_markdown"], "## Summary\nKey point 1.\n")

    def test_returns_transcript_by_default(self):
        result, _ = self._run_with_tmp_audio()
        self.assertIn("transcript", result)
        # StorageManager stub returns "TEACHER: Hello class."
        self.assertIsNotNone(result["transcript"])

    def test_transcript_absent_when_include_transcript_false(self):
        result, _ = self._run_with_tmp_audio(include_transcript=False)
        self.assertNotIn("transcript", result)

    def test_returns_transcription_events(self):
        result, _ = self._run_with_tmp_audio()
        self.assertIn("transcription_events", result)
        self.assertIsInstance(result["transcription_events"], list)
        self.assertEqual(len(result["transcription_events"]), 2)

    def test_uses_provided_session_id(self):
        result, _ = self._run_with_tmp_audio(session_id="custom-session-42")
        self.assertEqual(result["session_id"], "custom-session-42")

    def test_pipeline_run_transcription_called_once(self):
        _, mock_pipeline = self._run_with_tmp_audio()
        mock_pipeline.run_transcription.assert_called_once()

    def test_pipeline_run_summarizer_called_once(self):
        _, mock_pipeline = self._run_with_tmp_audio()
        mock_pipeline.run_summarizer.assert_called_once()

    def test_empty_summary_tokens(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            mock_pipeline = _make_mock_pipeline(summary_tokens=[])
            skill = AudioSummarySkill(
                pipeline_factory=_make_pipeline_factory(mock_pipeline)
            )
            result = skill.invoke(audio_file=tmp_path)
            self.assertEqual(result["summary_markdown"], "")
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Tests: pipeline factory failure
# ---------------------------------------------------------------------------

class TestAudioSummarySkillPipelineFailure(unittest.TestCase):
    def test_raises_runtime_error_on_pipeline_init_failure(self):
        def broken_factory(session_id):
            raise RuntimeError("Model load failed")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            skill = AudioSummarySkill(pipeline_factory=broken_factory)
            with self.assertRaises(RuntimeError) as ctx:
                skill.invoke(audio_file=tmp_path)
            self.assertIn("Pipeline initialisation failed", str(ctx.exception))
        finally:
            os.unlink(tmp_path)

    def test_raises_runtime_error_on_transcription_failure(self):
        mock_pipeline = MagicMock()
        mock_pipeline.run_transcription = MagicMock(side_effect=RuntimeError("ASR crashed"))

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            skill = AudioSummarySkill(
                pipeline_factory=_make_pipeline_factory(mock_pipeline)
            )
            with self.assertRaises(RuntimeError) as ctx:
                skill.invoke(audio_file=tmp_path)
            self.assertIn("Transcription failed", str(ctx.exception))
        finally:
            os.unlink(tmp_path)

    def test_raises_runtime_error_on_summarization_failure(self):
        mock_pipeline = _make_mock_pipeline()
        mock_pipeline.run_summarizer = MagicMock(side_effect=RuntimeError("LLM crashed"))

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            skill = AudioSummarySkill(
                pipeline_factory=_make_pipeline_factory(mock_pipeline)
            )
            with self.assertRaises(RuntimeError) as ctx:
                skill.invoke(audio_file=tmp_path)
            self.assertIn("Summarisation failed", str(ctx.exception))
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Tests: convenience function run_audio_summary
# ---------------------------------------------------------------------------

class TestRunAudioSummaryFunction(unittest.TestCase):
    def test_delegates_to_skill(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            mock_pipeline = _make_mock_pipeline()
            result = run_audio_summary(
                audio_file=tmp_path,
                pipeline_factory=_make_pipeline_factory(mock_pipeline),
            )
            self.assertIn("session_id", result)
            self.assertIn("summary_markdown", result)
        finally:
            os.unlink(tmp_path)

    def test_raises_input_error_without_audio(self):
        with self.assertRaises(AudioSummaryInputError):
            run_audio_summary()


if __name__ == "__main__":
    unittest.main()
