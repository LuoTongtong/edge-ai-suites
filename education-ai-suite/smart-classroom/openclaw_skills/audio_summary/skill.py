#
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#

"""
OpenClaw skill adapter – Smart Classroom Audio Summary
======================================================

Transcribes a classroom audio file and generates a structured Markdown
summary using the existing Smart Classroom ASR + LLM pipeline.

Entry point
-----------
  from openclaw_skills.audio_summary.skill import run

Input schema  (see manifest.json for the full JSON Schema)
------------
  {
    "audio_file":        str | None  – absolute path to the audio file,
    "audio_filename":    str | None  – filename already in the upload dir,
    "session_id":        str | None  – reuse an existing session,
    "source_type":       str         – "audio_file" (default) | "microphone",
    "include_transcript": bool       – include full transcript in output (default True)
  }
  At least one of ``audio_file`` or ``audio_filename`` must be provided.

Output schema (see manifest.json)
-------------
  {
    "session_id":        str  – Smart Classroom session identifier,
    "summary_markdown":  str  – generated Markdown summary,
    "transcript":        str  – full transcription text (when include_transcript=True),
    "source":            str  – "audio_file" | "audio_filename"
  }
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Execute the smart_classroom_audio_summary skill.

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
    audio_file: str | None = raw.get("audio_file")
    audio_filename: str | None = raw.get("audio_filename")

    if not audio_file and not audio_filename:
        raise ValueError(
            "At least one of 'audio_file' or 'audio_filename' must be provided."
        )

    if audio_file and not os.path.isfile(audio_file):
        raise ValueError(f"audio_file does not exist: {audio_file!r}")

    source_type = raw.get("source_type", "audio_file")
    if source_type not in ("audio_file", "microphone"):
        raise ValueError(
            f"Invalid source_type {source_type!r}. Must be 'audio_file' or 'microphone'."
        )

    return {
        "audio_file": audio_file,
        "audio_filename": audio_filename,
        "session_id": raw.get("session_id"),
        "source_type": source_type,
        "include_transcript": bool(raw.get("include_transcript", True)),
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _execute(params: dict[str, Any]) -> dict[str, Any]:
    # Late import so that tests can monkey-patch Pipeline without loading models.
    from pipeline import Pipeline  # noqa: PLC0415
    from dto.transcription_dto import TranscriptionRequest  # noqa: PLC0415
    from dto.audiosource import AudioSource  # noqa: PLC0415
    from utils.runtime_config_loader import RuntimeConfig  # noqa: PLC0415
    from utils.storage_manager import StorageManager  # noqa: PLC0415

    session_id: str | None = params["session_id"]
    pipeline = Pipeline(session_id)
    session_id = pipeline.session_id

    # ------------------------------------------------------------------
    # Determine audio filename / path
    # ------------------------------------------------------------------
    audio_file: str | None = params["audio_file"]
    audio_filename: str | None = params["audio_filename"]
    source: str

    if audio_file:
        # Copy / reference the file into the upload area if needed
        audio_filename = os.path.basename(audio_file)
        source = "audio_file"
    else:
        source = "audio_filename"

    source_type_str = params["source_type"]
    source_type_enum = (
        AudioSource.AUDIO_FILE
        if source_type_str == "audio_file"
        else AudioSource.MICROPHONE
    )

    transcription_request = TranscriptionRequest(
        audio_filename=audio_filename,
        source_type=source_type_enum,
    )

    # If caller supplied an absolute path, point the pipeline to that file
    # by adjusting the request so the ASR component can locate it.
    if audio_file:
        # The ASR component resolves filenames relative to the upload directory.
        # Override with the full path when an absolute path is supplied.
        transcription_request.audio_filename = audio_file

    # ------------------------------------------------------------------
    # Run transcription (streaming generator – collect all chunks)
    # ------------------------------------------------------------------
    logger.info("[audio_summary] Starting transcription for session %s", session_id)
    transcription_events: list[dict] = []
    for chunk in pipeline.run_transcription(transcription_request):
        transcription_events.append(chunk)

    # ------------------------------------------------------------------
    # Read back the transcription text that was persisted by the pipeline
    # ------------------------------------------------------------------
    transcript_text = ""
    if params["include_transcript"]:
        project_config = RuntimeConfig.get_section("Project")
        transcription_path = os.path.join(
            project_config.get("location"),
            project_config.get("name"),
            session_id,
            "transcription.txt",
        )
        try:
            transcript_text = StorageManager.read_text_file(transcription_path) or ""
        except FileNotFoundError:
            logger.warning("[audio_summary] transcription.txt not found for session %s", session_id)

    # ------------------------------------------------------------------
    # Run summarizer (streaming generator – collect all tokens)
    # ------------------------------------------------------------------
    logger.info("[audio_summary] Starting summarization for session %s", session_id)
    summary_tokens: list[str] = []
    for token in pipeline.run_summarizer():
        if isinstance(token, str):
            summary_tokens.append(token)
        elif isinstance(token, dict):
            # Some providers yield {"token": "..."} dicts
            summary_tokens.append(token.get("token", ""))

    summary_markdown = "".join(summary_tokens)

    result: dict[str, Any] = {
        "session_id": session_id,
        "summary_markdown": summary_markdown,
        "source": source,
    }
    if params["include_transcript"]:
        result["transcript"] = transcript_text

    logger.info(
        "[audio_summary] Completed. session_id=%s summary_length=%d",
        session_id,
        len(summary_markdown),
    )
    return result
