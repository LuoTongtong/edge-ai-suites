#
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#

"""
OpenClaw skill adapter – Smart Classroom MindMap
================================================

Generates a jsMind-compatible JSON mind map from a classroom summary.
Accepts either a Smart Classroom ``session_id`` (to read the persisted
``summary.md``) or a ``summary_markdown`` string (e.g. the output of the
``smart_classroom_audio_summary`` skill).  Both can be supplied together.

Entry point
-----------
  from openclaw_skills.mindmap.skill import run

Input schema  (see manifest.json for the full JSON Schema)
------------
  {
    "session_id":        str | None  – existing Smart Classroom session ID,
    "summary_markdown":  str | None  – Markdown summary text,
    "language":          str         – "en" (default) | "zh",
    "output_format":     str         – "jsmind_json" (default) | "raw",
    "include_raw":       bool        – always include raw LLM string (default False)
  }
  At least one of ``session_id`` or ``summary_markdown`` must be provided.

Output schema (see manifest.json)
-------------
  {
    "session_id":   str          – session used / created,
    "mindmap":      dict | None  – parsed jsMind JSON object (None when parsing failed),
    "raw_mindmap":  str          – raw LLM output string,
    "format":       str          – "jsmind_json" | "raw",
    "source":       str          – "session_id" | "summary_markdown"
  }

Chaining with smart_classroom_audio_summary
-------------------------------------------
  audio_result = smart_classroom_audio_summary.run({
      "audio_file": "/data/class.mp3",
      "include_transcript": True,
  })
  mindmap_result = smart_classroom_mindmap.run({
      "session_id":       audio_result["session_id"],
      "summary_markdown": audio_result["summary_markdown"],
      "output_format":    "jsmind_json",
  })

Notes
-----
- The existing pipeline enforces a minimum token count (``mindmap.min_token``
  in ``config.yaml``).  When the summary is too short the skill returns the
  same ``insufficient_input`` jsMind JSON that the REST endpoint returns,
  with ``mindmap`` populated and ``format`` set to ``"jsmind_json"``.
- The output format is always ``jsmind_json`` because the LLM prompt in
  ``config.yaml`` requests jsMind JSON.  The ``output_format`` parameter is
  exposed for future extensibility.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Execute the smart_classroom_mindmap skill.

    Parameters
    ----------
    input:
        Dictionary matching the skill's input schema.

    Returns
    -------
    dict
        Dictionary matching the skill's output schema.

    Raises
    ------
    ValueError
        If the input fails validation.
    """
    validated = _validate_input(input)
    return _execute(validated)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_input(raw: dict[str, Any]) -> dict[str, Any]:
    session_id: str | None = raw.get("session_id") or None
    summary_markdown: str | None = raw.get("summary_markdown") or None

    if not session_id and not summary_markdown:
        raise ValueError(
            "At least one of 'session_id' or 'summary_markdown' must be provided."
        )

    language = raw.get("language", "en")
    if language not in ("en", "zh"):
        raise ValueError(f"Invalid language {language!r}. Must be 'en' or 'zh'.")

    output_format = raw.get("output_format", "jsmind_json")
    if output_format not in ("jsmind_json", "raw"):
        raise ValueError(
            f"Invalid output_format {output_format!r}. Must be 'jsmind_json' or 'raw'."
        )

    return {
        "session_id": session_id,
        "summary_markdown": summary_markdown,
        "language": language,
        "output_format": output_format,
        "include_raw": bool(raw.get("include_raw", False)),
    }


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------


def _write_summary_to_session(session_id: str, summary_markdown: str) -> None:
    """Persist *summary_markdown* as ``summary.md`` in the session directory.

    This lets ``Pipeline.run_mindmap()`` read the caller-supplied summary
    instead of requiring an existing session artifact.
    """
    from utils.runtime_config_loader import RuntimeConfig  # noqa: PLC0415
    from utils.storage_manager import StorageManager  # noqa: PLC0415

    project_config = RuntimeConfig.get_section("Project")
    session_dir = os.path.join(
        project_config.get("location"),
        project_config.get("name"),
        session_id,
    )
    summary_path = os.path.join(session_dir, "summary.md")
    os.makedirs(session_dir, exist_ok=True)
    StorageManager.save(summary_path, summary_markdown, append=False)
    logger.debug("[mindmap] Wrote summary.md to %s", summary_path)


def _run_pipeline_mindmap(session_id: str) -> str:
    """Call ``Pipeline(session_id).run_mindmap()`` and return the raw string."""
    from pipeline import Pipeline  # noqa: PLC0415

    pipeline = Pipeline(session_id)
    return pipeline.run_mindmap()


def _parse_mindmap_json(raw: str) -> dict | None:
    """Try to parse *raw* as JSON.

    Returns the parsed dict on success, ``None`` on failure.
    Strips common LLM artefacts such as markdown code fences before parsing.
    """
    if not raw:
        return None

    candidate = raw.strip()

    # Strip optional markdown code fences: ```json … ```
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner_lines = lines[1:] if len(lines) > 1 else lines
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        candidate = "\n".join(inner_lines).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        logger.debug("[mindmap] JSON parse failed; returning raw string")
        return None


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------


def _execute(params: dict[str, Any]) -> dict[str, Any]:
    from utils.session_manager import generate_session_id  # noqa: PLC0415

    session_id: str | None = params["session_id"]
    summary_markdown: str | None = params["summary_markdown"]
    output_format: str = params["output_format"]
    include_raw: bool = params["include_raw"]

    # ------------------------------------------------------------------
    # Decide source and prepare session
    # ------------------------------------------------------------------
    if summary_markdown:
        # When summary_markdown is supplied we write it into a session so
        # that Pipeline.run_mindmap() can pick it up from summary.md.
        if not session_id:
            session_id = generate_session_id()
            logger.info(
                "[mindmap] No session_id supplied; created ephemeral session %s", session_id
            )
        _write_summary_to_session(session_id, summary_markdown)
        source = "summary_markdown"
    else:
        # session_id must be set (validated above)
        source = "session_id"

    # ------------------------------------------------------------------
    # Generate mindmap via the existing Pipeline
    # ------------------------------------------------------------------
    logger.info("[mindmap] Running Pipeline.run_mindmap() for session %s", session_id)
    raw_mindmap: str = _run_pipeline_mindmap(session_id)

    # ------------------------------------------------------------------
    # Parse JSON
    # ------------------------------------------------------------------
    mindmap_obj: dict | None = _parse_mindmap_json(raw_mindmap)
    effective_format = "jsmind_json" if mindmap_obj is not None else "raw"

    # ------------------------------------------------------------------
    # Build output
    # ------------------------------------------------------------------
    result: dict[str, Any] = {
        "session_id": session_id,
        "mindmap": mindmap_obj,
        "format": effective_format,
        "source": source,
    }

    if include_raw or mindmap_obj is None:
        result["raw_mindmap"] = raw_mindmap

    logger.info(
        "[mindmap] Completed. session_id=%s format=%s source=%s",
        session_id,
        effective_format,
        source,
    )
    return result
