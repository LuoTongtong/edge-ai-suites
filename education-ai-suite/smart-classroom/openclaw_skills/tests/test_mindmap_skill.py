#
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#

"""
Lightweight unit tests for the smart_classroom_mindmap OpenClaw skill.

No LLM or ASR models are loaded: Pipeline.run_mindmap() and all storage
utilities are fully mocked so tests run without the Smart Classroom runtime.
"""

import importlib
import importlib.util
import json
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs for heavy modules not installed in the test env
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


def _ensure_stubs() -> None:
    for pkg in [
        "pipeline",
        "utils",
        "utils.runtime_config_loader",
        "utils.storage_manager",
        "utils.session_manager",
        "utils.markdown_cleaner",
    ]:
        _make_stub(pkg)

    class _RuntimeConfig:
        @staticmethod
        def get_section(section):
            return {"location": "/tmp/sc_test", "name": "smart-classroom"}

    sys.modules["utils.runtime_config_loader"].RuntimeConfig = _RuntimeConfig  # type: ignore[attr-defined]

    class _StorageManager:
        _saved: dict = {}

        @staticmethod
        def save(path, content, append=False):
            _StorageManager._saved[path] = content

        @staticmethod
        def read_text_file(path):
            return _StorageManager._saved.get(path, "")

    sys.modules["utils.storage_manager"].StorageManager = _StorageManager  # type: ignore[attr-defined]
    sys.modules["utils.session_manager"].generate_session_id = lambda: "test-mindmap-session"  # type: ignore[attr-defined]
    sys.modules["utils.markdown_cleaner"].markdown_to_plain = lambda t: t  # type: ignore[attr-defined]


_ensure_stubs()

_SKILL_PATH = pathlib.Path(__file__).parent.parent / "mindmap" / "skill.py"
_spec = importlib.util.spec_from_file_location("_mindmap_skill", _SKILL_PATH)
_skill_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_skill_mod)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_JSMIND_JSON = json.dumps({
    "meta": {"name": "physics_lecture", "author": "ai_assistant", "version": "1.0"},
    "format": "node_tree",
    "data": {
        "id": "root",
        "topic": "Quantum Mechanics",
        "children": [
            {"id": "wpd", "topic": "Wave-Particle Duality", "children": []}
        ]
    }
})

_INSUFFICIENT_JSMIND = json.dumps({
    "meta": {"name": "insufficient_input", "author": "ai_assistant", "version": "1.0"},
    "format": "node_tree",
    "data": {
        "id": "root",
        "topic": "Insufficient Input",
        "children": [
            {
                "id": "insufficient_info",
                "topic": "Insufficient Information",
                "children": [
                    {"id": "short_summary", "topic": "The summary is too short to generate a meaningful mindmap"},
                    {"id": "token_info", "topic": "Current tokens: 5, Required: 20"}
                ]
            }
        ]
    }
})


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------

class TestMindmapInputValidation(unittest.TestCase):

    def test_missing_both_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _skill_mod._validate_input({})
        self.assertIn("session_id", str(ctx.exception).lower())

    def test_invalid_language_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _skill_mod._validate_input({"session_id": "s1", "language": "fr"})
        self.assertIn("language", str(ctx.exception))

    def test_invalid_output_format_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _skill_mod._validate_input({"session_id": "s1", "output_format": "mermaid"})
        self.assertIn("output_format", str(ctx.exception))

    def test_session_id_only_is_valid(self):
        result = _skill_mod._validate_input({"session_id": "abc"})
        self.assertEqual(result["session_id"], "abc")
        self.assertIsNone(result["summary_markdown"])

    def test_summary_markdown_only_is_valid(self):
        result = _skill_mod._validate_input({"summary_markdown": "## Summary\n- point"})
        self.assertIsNone(result["session_id"])
        self.assertIn("Summary", result["summary_markdown"])

    def test_both_fields_accepted(self):
        result = _skill_mod._validate_input({
            "session_id": "s1",
            "summary_markdown": "## Summary\n- point",
        })
        self.assertEqual(result["session_id"], "s1")
        self.assertIsNotNone(result["summary_markdown"])

    def test_defaults(self):
        result = _skill_mod._validate_input({"session_id": "s1"})
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["output_format"], "jsmind_json")
        self.assertFalse(result["include_raw"])


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------

class TestMindmapJsonParsing(unittest.TestCase):

    def test_valid_json_parsed(self):
        result = _skill_mod._parse_mindmap_json(_VALID_JSMIND_JSON)
        self.assertIsNotNone(result)
        self.assertIn("meta", result)
        self.assertIn("data", result)

    def test_json_with_code_fence_parsed(self):
        fenced = "```json\n" + _VALID_JSMIND_JSON + "\n```"
        result = _skill_mod._parse_mindmap_json(fenced)
        self.assertIsNotNone(result)
        self.assertIn("meta", result)

    def test_invalid_json_returns_none(self):
        result = _skill_mod._parse_mindmap_json("This is not JSON at all.")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = _skill_mod._parse_mindmap_json("")
        self.assertIsNone(result)

    def test_none_returns_none(self):
        result = _skill_mod._parse_mindmap_json(None)  # type: ignore[arg-type]
        self.assertIsNone(result)

    def test_insufficient_input_json_parsed(self):
        result = _skill_mod._parse_mindmap_json(_INSUFFICIENT_JSMIND)
        self.assertIsNotNone(result)
        self.assertEqual(result["meta"]["name"], "insufficient_input")


# ---------------------------------------------------------------------------
# Output shaping tests
# ---------------------------------------------------------------------------

class TestMindmapOutputShaping(unittest.TestCase):

    def _run_with_mock_pipeline(self, params: dict, raw_mindmap: str) -> dict:
        """Run _execute() with a mocked _run_pipeline_mindmap."""
        with patch.object(_skill_mod, "_run_pipeline_mindmap", return_value=raw_mindmap):
            with patch.object(_skill_mod, "_write_summary_to_session"):
                return _skill_mod._execute(params)

    def test_session_id_source(self):
        params = {
            "session_id": "existing-session",
            "summary_markdown": None,
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, _VALID_JSMIND_JSON)
        self.assertEqual(result["source"], "session_id")
        self.assertEqual(result["session_id"], "existing-session")

    def test_summary_markdown_source(self):
        params = {
            "session_id": None,
            "summary_markdown": "## Summary\n- point",
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, _VALID_JSMIND_JSON)
        self.assertEqual(result["source"], "summary_markdown")

    def test_both_provided_uses_summary_markdown_source(self):
        params = {
            "session_id": "s1",
            "summary_markdown": "## Summary\n- point",
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, _VALID_JSMIND_JSON)
        # summary_markdown takes precedence for source label
        self.assertEqual(result["source"], "summary_markdown")
        # But the provided session_id is used
        self.assertEqual(result["session_id"], "s1")

    def test_valid_json_parsed_into_mindmap_field(self):
        params = {
            "session_id": "s1",
            "summary_markdown": None,
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, _VALID_JSMIND_JSON)
        self.assertIsNotNone(result["mindmap"])
        self.assertIsInstance(result["mindmap"], dict)
        self.assertEqual(result["format"], "jsmind_json")

    def test_invalid_json_sets_mindmap_to_none(self):
        params = {
            "session_id": "s1",
            "summary_markdown": None,
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, "Here is your mindmap: sorry, text only")
        self.assertIsNone(result["mindmap"])
        self.assertEqual(result["format"], "raw")
        self.assertIn("raw_mindmap", result)

    def test_include_raw_true_adds_raw_field_on_success(self):
        params = {
            "session_id": "s1",
            "summary_markdown": None,
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": True,
        }
        result = self._run_with_mock_pipeline(params, _VALID_JSMIND_JSON)
        self.assertIn("raw_mindmap", result)
        self.assertEqual(result["raw_mindmap"], _VALID_JSMIND_JSON)

    def test_include_raw_false_no_raw_field_on_success(self):
        params = {
            "session_id": "s1",
            "summary_markdown": None,
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, _VALID_JSMIND_JSON)
        self.assertNotIn("raw_mindmap", result)

    def test_insufficient_input_json_parsed_normally(self):
        params = {
            "session_id": "s1",
            "summary_markdown": None,
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, _INSUFFICIENT_JSMIND)
        self.assertIsNotNone(result["mindmap"])
        self.assertEqual(result["mindmap"]["meta"]["name"], "insufficient_input")
        self.assertEqual(result["format"], "jsmind_json")

    def test_new_session_created_when_no_session_id(self):
        params = {
            "session_id": None,
            "summary_markdown": "## Summary\n- point",
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        result = self._run_with_mock_pipeline(params, _VALID_JSMIND_JSON)
        # generate_session_id stub returns "test-mindmap-session"
        self.assertEqual(result["session_id"], "test-mindmap-session")

    def test_write_summary_called_when_markdown_provided(self):
        params = {
            "session_id": "s1",
            "summary_markdown": "## Summary\n- point",
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        with patch.object(_skill_mod, "_run_pipeline_mindmap", return_value=_VALID_JSMIND_JSON):
            with patch.object(_skill_mod, "_write_summary_to_session") as mock_write:
                _skill_mod._execute(params)
                mock_write.assert_called_once_with("s1", "## Summary\n- point")

    def test_write_summary_not_called_when_only_session_id(self):
        params = {
            "session_id": "s1",
            "summary_markdown": None,
            "language": "en",
            "output_format": "jsmind_json",
            "include_raw": False,
        }
        with patch.object(_skill_mod, "_run_pipeline_mindmap", return_value=_VALID_JSMIND_JSON):
            with patch.object(_skill_mod, "_write_summary_to_session") as mock_write:
                _skill_mod._execute(params)
                mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: run() entry point
# ---------------------------------------------------------------------------

class TestMindmapRunEntryPoint(unittest.TestCase):

    def test_run_validates_and_executes(self):
        with patch.object(_skill_mod, "_run_pipeline_mindmap", return_value=_VALID_JSMIND_JSON):
            with patch.object(_skill_mod, "_write_summary_to_session"):
                result = _skill_mod.run({"session_id": "s1"})
        self.assertIn("session_id", result)
        self.assertIn("mindmap", result)
        self.assertIn("format", result)
        self.assertIn("source", result)

    def test_run_raises_on_invalid_input(self):
        with self.assertRaises(ValueError):
            _skill_mod.run({})


if __name__ == "__main__":
    unittest.main()
