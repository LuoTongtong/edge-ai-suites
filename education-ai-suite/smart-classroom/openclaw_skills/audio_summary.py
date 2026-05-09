"""
OpenClaw skill adapter for Smart Classroom audio summary.

This module wraps the existing Smart Classroom Pipeline to expose an
``audio_summary`` skill that OpenClaw (or any Python caller) can invoke
without requiring the FastAPI server to be running.

Typical usage
-------------
>>> from openclaw_skills.audio_summary import run_audio_summary
>>> result = run_audio_summary(audio_file="/path/to/lecture.mp3")
>>> print(result["summary_markdown"])
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

class AudioSummaryInputError(ValueError):
    """Raised when the skill receives invalid or missing input."""


def _resolve_audio_path(
    audio_file: Optional[str],
    audio_filename: Optional[str],
) -> str:
    """Return the absolute path to the audio file that should be transcribed.

    Priority
    --------
    1. ``audio_file`` — caller supplies a full (or relative) path directly.
    2. ``audio_filename`` — caller supplies a filename that was already staged
       under the project storage directory by a prior ``/upload-audio`` call.
       The function resolves it against the configured storage location.

    Raises
    ------
    AudioSummaryInputError
        If neither ``audio_file`` nor ``audio_filename`` is provided, or if
        the resolved path does not exist.
    """
    if not audio_file and not audio_filename:
        raise AudioSummaryInputError(
            "Either 'audio_file' (full path) or 'audio_filename' (staged name) "
            "must be provided."
        )

    if audio_file:
        path = os.path.abspath(audio_file)
        if not os.path.isfile(path):
            raise AudioSummaryInputError(
                f"audio_file not found: {path}"
            )
        return path

    # audio_filename — resolve against project storage
    try:
        from utils.runtime_config_loader import RuntimeConfig  # noqa: PLC0415
        project_config = RuntimeConfig.get_section("Project")
    except Exception as exc:  # pragma: no cover
        raise AudioSummaryInputError(
            f"Could not read runtime config to resolve audio_filename: {exc}"
        ) from exc

    staged_path = os.path.join(
        project_config.get("location", "storage"),
        project_config.get("name", "smart-classroom"),
        "audio",
        audio_filename,
    )
    staged_path = os.path.abspath(staged_path)
    if not os.path.isfile(staged_path):
        raise AudioSummaryInputError(
            f"audio_filename '{audio_filename}' not found in staged storage: {staged_path}"
        )
    return staged_path


# ---------------------------------------------------------------------------
# Skill adapter class
# ---------------------------------------------------------------------------

class AudioSummarySkill:
    """OpenClaw-callable skill that transcribes classroom audio and summarises it.

    Parameters
    ----------
    pipeline_factory:
        Optional callable that returns a ``Pipeline`` instance for a given
        ``session_id``.  Defaults to importing and instantiating
        ``pipeline.Pipeline``.  Pass a mock factory in tests to avoid
        loading heavy ASR/LLM models.
    """

    SKILL_NAME = "smart_classroom_audio_summary"

    def __init__(self, pipeline_factory=None):
        self._pipeline_factory = pipeline_factory or self._default_pipeline_factory

    @staticmethod
    def _default_pipeline_factory(session_id: str):
        from pipeline import Pipeline  # noqa: PLC0415
        return Pipeline(session_id)

    # ------------------------------------------------------------------
    # Public invoke method
    # ------------------------------------------------------------------

    def invoke(
        self,
        *,
        audio_file: Optional[str] = None,
        audio_filename: Optional[str] = None,
        session_id: Optional[str] = None,
        source_type: str = "audio_file",
        include_transcript: bool = True,
    ) -> Dict[str, Any]:
        """Run the audio summary skill.

        Parameters
        ----------
        audio_file:
            Absolute or relative path to an audio file accessible on disk.
        audio_filename:
            Filename of audio already staged under the project storage
            directory (e.g. uploaded via ``/upload-audio``).
        session_id:
            Optional existing session id.  A new one is generated when omitted.
        source_type:
            ``"audio_file"`` (default) or ``"microphone"``.  Currently only
            ``"audio_file"`` is supported by the skill adapter.
        include_transcript:
            When ``True`` (default) the returned dict contains the full
            ``transcript`` text read from ``transcription.txt``.

        Returns
        -------
        dict
            A structured result with at least:

            * ``session_id`` — the session used
            * ``summary_markdown`` — full generated summary (Markdown string)
            * ``transcript`` — full transcription text (if ``include_transcript``)
            * ``transcription_events`` — list of raw chunk dicts from the pipeline
        """
        # ---- validate source_type ----
        if source_type not in ("audio_file", "microphone"):
            raise AudioSummaryInputError(
                f"Unsupported source_type '{source_type}'. Use 'audio_file'."
            )
        if source_type == "microphone":
            raise AudioSummaryInputError(
                "source_type='microphone' is not supported by the skill adapter. "
                "Use source_type='audio_file' and provide an audio_file path."
            )

        # ---- resolve audio path ----
        resolved_path = _resolve_audio_path(audio_file, audio_filename)
        logger.info("AudioSummarySkill: resolved audio path → %s", resolved_path)

        # ---- build session ----
        if not session_id:
            from utils.session_manager import generate_session_id  # noqa: PLC0415
            session_id = generate_session_id()
        logger.info("AudioSummarySkill: session_id=%s", session_id)

        # ---- build TranscriptionRequest ----
        from dto.transcription_dto import TranscriptionRequest  # noqa: PLC0415
        from dto.audiosource import AudioSource  # noqa: PLC0415

        transcription_request = TranscriptionRequest(
            audio_filename=resolved_path,
            source_type=AudioSource.AUDIO_FILE,
        )

        # ---- instantiate pipeline ----
        try:
            pipeline = self._pipeline_factory(session_id)
        except Exception as exc:
            logger.exception("Failed to instantiate Pipeline for session %s", session_id)
            raise RuntimeError(
                f"Pipeline initialisation failed for session '{session_id}': {exc}"
            ) from exc

        # ---- run transcription ----
        transcription_events: List[Dict[str, Any]] = []
        try:
            for chunk in pipeline.run_transcription(transcription_request):
                transcription_events.append(chunk)
                logger.debug("Transcription chunk: %s", chunk.get("event", "data"))
        except Exception as exc:
            logger.exception("Transcription failed for session %s", session_id)
            raise RuntimeError(
                f"Transcription failed for session '{session_id}': {exc}"
            ) from exc

        # ---- read transcript file (written by ASRComponent) ----
        transcript: Optional[str] = None
        if include_transcript:
            transcript = self._read_session_file(session_id, "transcription.txt")

        # ---- run summariser ----
        summary_tokens: List[str] = []
        try:
            for token in pipeline.run_summarizer():
                summary_tokens.append(token)
        except Exception as exc:
            logger.exception("Summarisation failed for session %s", session_id)
            raise RuntimeError(
                f"Summarisation failed for session '{session_id}': {exc}"
            ) from exc

        summary_markdown = "".join(summary_tokens)

        # ---- build output ----
        result: Dict[str, Any] = {
            "session_id": session_id,
            "summary_markdown": summary_markdown,
            "transcription_events": transcription_events,
        }
        if include_transcript:
            result["transcript"] = transcript

        logger.info(
            "AudioSummarySkill: completed session=%s summary_length=%d",
            session_id,
            len(summary_markdown),
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_session_file(session_id: str, filename: str) -> Optional[str]:
        """Read a text artifact written by the pipeline for *session_id*."""
        try:
            from utils.runtime_config_loader import RuntimeConfig  # noqa: PLC0415
            from utils.storage_manager import StorageManager  # noqa: PLC0415

            project_config = RuntimeConfig.get_section("Project")
            path = os.path.join(
                project_config.get("location", "storage"),
                project_config.get("name", "smart-classroom"),
                session_id,
                filename,
            )
            return StorageManager.read_text_file(path)
        except FileNotFoundError:
            logger.warning("Session file not found: %s/%s", session_id, filename)
            return None
        except Exception as exc:
            logger.warning("Could not read %s/%s: %s", session_id, filename, exc)
            return None


# ---------------------------------------------------------------------------
# Convenience function (OpenClaw function-call style)
# ---------------------------------------------------------------------------

def run_audio_summary(
    audio_file: Optional[str] = None,
    audio_filename: Optional[str] = None,
    session_id: Optional[str] = None,
    source_type: str = "audio_file",
    include_transcript: bool = True,
    *,
    pipeline_factory=None,
) -> Dict[str, Any]:
    """Convenience wrapper around :class:`AudioSummarySkill`.

    This is the primary entry point for OpenClaw invocations.

    Parameters
    ----------
    audio_file:
        Path to an audio file accessible on disk.
    audio_filename:
        Filename already staged under the project storage directory.
    session_id:
        Optional existing session id.
    source_type:
        ``"audio_file"`` (default).
    include_transcript:
        Include the full transcript in the output (default ``True``).
    pipeline_factory:
        Optional factory for dependency injection / testing.

    Returns
    -------
    dict
        See :meth:`AudioSummarySkill.invoke`.

    Examples
    --------
    >>> result = run_audio_summary(audio_file="/data/lecture.wav")
    >>> print(result["summary_markdown"])
    """
    skill = AudioSummarySkill(pipeline_factory=pipeline_factory)
    return skill.invoke(
        audio_file=audio_file,
        audio_filename=audio_filename,
        session_id=session_id,
        source_type=source_type,
        include_transcript=include_transcript,
    )
