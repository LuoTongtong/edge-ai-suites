#
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#

"""
Unit tests for the OpenClaw proxy endpoints.

All tests mock the OpenClaw HTTP client (httpx.AsyncClient.post) and FastAPI
dependency injection so that no real network calls or model loading occurs.

Run from the smart-classroom directory:
    python -m pytest openclaw_skills/tests/test_openclaw_proxy.py -v
"""

import os
import sys
import types
import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Stub out modules that are imported at endpoints.py / main.py load time so
# we can import the proxy module without the full Smart Classroom runtime.
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


def _stub_heavy_deps() -> None:
    for pkg in [
        # utils
        "utils", "utils.config_loader", "utils.runtime_config_loader",
        "utils.storage_manager", "utils.session_manager", "utils.locks",
        "utils.logger_config", "utils.session_state_manager",
        "utils.content_search_client", "utils.media_validation_service",
        "utils.markdown_cleaner", "utils.platform_info", "utils.audio_util",
        "utils.ov_genai_util", "utils.system_checker",
        # dto
        "dto", "dto.audiosource", "dto.transcription_dto",
        "dto.summarizer_dto", "dto.project_settings",
        "dto.video_analytics_dto", "dto.video_metadata_dto",
        "dto.search_dto", "dto.ocr_dto",
        # components
        "components", "components.base_component", "components.stream_reader",
        "components.asr_component", "components.summarizer_component",
        "components.mindmap_component", "components.segmentation",
        "components.segmentation.content_segmentation",
        "components.ffmpeg", "components.ffmpeg.audio_preprocessing",
        "components.llm", "components.llm.ipex",
        "components.llm.ipex.summarizer",
        "components.llm.openvino", "components.llm.openvino.summarizer",
        "components.llm.openvino_genai",
        "components.llm.openvino_genai.summarizer",
        "components.va", "components.va.va_pipeline_service",
        "components.va.media_service",
        "components.ocr", "components.ocr.ocr_pipeline",
        "components.asr",
        # monitoring
        "monitoring", "monitoring.monitor",
        # pipeline
        "pipeline",
        # ensure_model / preload_models
        "utils.ensure_model", "utils.preload_models",
    ]:
        _make_stub(pkg)

    # Minimal pydantic stub if not installed (fastapi uses it, so it usually is)
    try:
        import pydantic  # noqa: F401
    except ImportError:
        pydantic_mod = _make_stub("pydantic")

        class _BaseModel:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        pydantic_mod.BaseModel = _BaseModel
        pydantic_mod.Field = lambda *a, **kw: None


_stub_heavy_deps()

# Ensure the smart-classroom root is on sys.path
_SC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SC_ROOT not in sys.path:
    sys.path.insert(0, _SC_ROOT)

# Now we can safely import the proxy module
from api.openclaw_proxy import (  # noqa: E402
    _get_base_url,
    _build_headers,
    _get_timeout,
    _call_openclaw,
    openclaw_router,
    OpenClawChatRequest,
    OpenClawAudioSummaryRequest,
    OpenClawMindmapRequest,
)

# Import FastAPI test client
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ---------------------------------------------------------------------------
# Shared test app
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(openclaw_router)
_client = TestClient(_test_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper: mock a successful httpx response
# ---------------------------------------------------------------------------

def _make_httpx_response(json_body: dict, status_code: int = 200):
    """Create a mock httpx.Response object."""
    import json as json_lib
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.is_success = 200 <= status_code < 300
    mock_resp.json = MagicMock(return_value=json_body)
    mock_resp.text = json_lib.dumps(json_body)
    return mock_resp


# ---------------------------------------------------------------------------
# Tests: configuration helpers
# ---------------------------------------------------------------------------

class TestConfigHelpers(unittest.TestCase):
    def test_get_base_url_raises_503_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCLAW_BASE_URL", None)
            from fastapi import HTTPException as _HTTPEx
            with self.assertRaises(_HTTPEx) as ctx:
                _get_base_url()
            self.assertEqual(ctx.exception.status_code, 503)

    def test_get_base_url_returns_stripped_url(self):
        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://host:8080/"}):
            self.assertEqual(_get_base_url(), "http://host:8080")

    def test_build_headers_no_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCLAW_API_KEY", None)
            headers = _build_headers()
            self.assertIn("Content-Type", headers)
            self.assertNotIn("Authorization", headers)

    def test_build_headers_with_api_key(self):
        with patch.dict(os.environ, {"OPENCLAW_API_KEY": "secret"}):
            headers = _build_headers()
            self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_get_timeout_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCLAW_TIMEOUT", None)
            self.assertEqual(_get_timeout(), 120.0)

    def test_get_timeout_custom(self):
        with patch.dict(os.environ, {"OPENCLAW_TIMEOUT": "30"}):
            self.assertEqual(_get_timeout(), 30.0)

    def test_get_timeout_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"OPENCLAW_TIMEOUT": "not-a-number"}):
            self.assertEqual(_get_timeout(), 120.0)


# ---------------------------------------------------------------------------
# Tests: /openclaw/chat endpoint
# ---------------------------------------------------------------------------

class TestChatEndpoint(unittest.TestCase):
    _PATCH_TARGET = "api.openclaw_proxy.httpx.AsyncClient"

    def _post_chat(self, body: dict):
        return _client.post("/openclaw/chat", json=body)

    def test_returns_503_when_base_url_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCLAW_BASE_URL", None)
            resp = self._post_chat({"message": "Hello"})
        self.assertEqual(resp.status_code, 503)

    def test_successful_chat(self):
        oc_response = {"result": "OK", "skill": "chat", "output": {"text": "Hi!"}}
        mock_resp = _make_httpx_response(oc_response)

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                resp = self._post_chat({"message": "Hello"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["result"], "OK")

    def test_chat_includes_session_context(self):
        """Session context fields are forwarded to OpenClaw."""
        captured = {}

        async def _fake_post(url, **kwargs):
            captured["payload"] = kwargs.get("json", {})
            return _make_httpx_response({"ok": True})

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = _fake_post

        body = {
            "message": "Summarize",
            "session_id": "sess-123",
            "audio_filename": "audio.mp3",
            "summary_markdown": "## Summary",
        }
        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                _client.post("/openclaw/chat", json=body)

        skill_input = captured["payload"]["input"]
        self.assertEqual(skill_input["session_id"], "sess-123")
        self.assertEqual(skill_input["audio_filename"], "audio.mp3")
        self.assertEqual(skill_input["summary_markdown"], "## Summary")

    def test_chat_returns_502_on_network_error(self):
        import httpx

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                resp = self._post_chat({"message": "Hello"})

        self.assertEqual(resp.status_code, 502)

    def test_chat_returns_502_on_non_2xx(self):
        mock_resp = _make_httpx_response({"error": "skill not found"}, status_code=404)

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                resp = self._post_chat({"message": "Hello"})

        self.assertEqual(resp.status_code, 502)

    def test_chat_returns_400_on_missing_message(self):
        resp = self._post_chat({})
        # FastAPI validates the request body; message is required
        self.assertIn(resp.status_code, (400, 422))


# ---------------------------------------------------------------------------
# Tests: /openclaw/skills/audio-summary endpoint
# ---------------------------------------------------------------------------

class TestAudioSummaryEndpoint(unittest.TestCase):
    _PATCH_TARGET = "api.openclaw_proxy.httpx.AsyncClient"

    def _post(self, body: dict):
        return _client.post("/openclaw/skills/audio-summary", json=body)

    def test_returns_422_when_no_audio_provided(self):
        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            resp = self._post({"session_id": "sess-abc"})
        self.assertEqual(resp.status_code, 422)

    def test_successful_audio_summary(self):
        oc_response = {
            "session_id": "sess-001",
            "summary_markdown": "## Summary\n- Point 1",
            "transcript": "TEACHER: Hello.",
            "transcription_events": [],
        }
        mock_resp = _make_httpx_response(oc_response)

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                resp = self._post({"audio_filename": "lecture.mp3"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["session_id"], "sess-001")
        self.assertIn("summary_markdown", data)

    def test_audio_file_and_filename_forwarded(self):
        captured = {}

        async def _fake_post(url, **kwargs):
            captured["payload"] = kwargs.get("json", {})
            return _make_httpx_response({"ok": True})

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = _fake_post

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                self._post(
                    {
                        "audio_file": "/data/lecture.wav",
                        "audio_filename": "lecture.wav",
                        "session_id": "s1",
                        "include_transcript": False,
                    }
                )

        skill_input = captured["payload"]["input"]
        self.assertEqual(skill_input["audio_file"], "/data/lecture.wav")
        self.assertEqual(skill_input["audio_filename"], "lecture.wav")
        self.assertEqual(skill_input["session_id"], "s1")
        self.assertFalse(skill_input["include_transcript"])
        self.assertEqual(captured["payload"]["skill"], "smart_classroom_audio_summary")

    def test_returns_503_when_base_url_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCLAW_BASE_URL", None)
            resp = self._post({"audio_filename": "lecture.mp3"})
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# Tests: /openclaw/skills/mindmap endpoint
# ---------------------------------------------------------------------------

class TestMindmapEndpoint(unittest.TestCase):
    _PATCH_TARGET = "api.openclaw_proxy.httpx.AsyncClient"

    def _post(self, body: dict):
        return _client.post("/openclaw/skills/mindmap", json=body)

    def test_returns_422_when_neither_session_nor_summary_provided(self):
        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            resp = self._post({"language": "en"})
        self.assertEqual(resp.status_code, 422)

    def test_successful_mindmap(self):
        oc_response = {
            "session_id": "sess-002",
            "mindmap": {
                "meta": {"name": "test", "author": "ai", "version": "1.0"},
                "format": "node_tree",
                "data": {"id": "root", "topic": "Main", "children": []},
            },
            "format": "jsmind_json",
            "source": "summary_markdown",
        }
        mock_resp = _make_httpx_response(oc_response)

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                resp = self._post({"summary_markdown": "## Topic\n- A\n- B"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["format"], "jsmind_json")

    def test_skill_name_is_correct(self):
        captured = {}

        async def _fake_post(url, **kwargs):
            captured["payload"] = kwargs.get("json", {})
            return _make_httpx_response({"ok": True})

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = _fake_post

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                self._post({"session_id": "sess-xyz"})

        self.assertEqual(captured["payload"]["skill"], "smart_classroom_mindmap")

    def test_returns_503_when_base_url_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENCLAW_BASE_URL", None)
            resp = self._post({"session_id": "abc"})
        self.assertEqual(resp.status_code, 503)

    def test_timeout_error_returns_502(self):
        import httpx

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                resp = self._post({"session_id": "abc"})

        self.assertEqual(resp.status_code, 502)

    def test_non_json_response_returns_502(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_success = True
        mock_resp.text = "not json"
        mock_resp.json = MagicMock(side_effect=ValueError("not JSON"))

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_instance.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "http://oc:8080"}):
            with patch(self._PATCH_TARGET, return_value=mock_client_instance):
                resp = self._post({"session_id": "abc"})

        self.assertEqual(resp.status_code, 502)


if __name__ == "__main__":
    unittest.main()
