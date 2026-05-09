#
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#

"""
OpenClaw proxy endpoints for Smart Classroom.

This module exposes a small set of FastAPI routes that the Smart Classroom UI
can call to invoke OpenClaw skills (``smart_classroom_audio_summary`` and
``smart_classroom_mindmap``) **without** exposing OpenClaw credentials to the
browser.

Architecture
------------

  Smart Classroom UI
    -> Smart Classroom backend (this module)
    -> OpenClaw runtime  (OPENCLAW_BASE_URL)
    -> Smart Classroom OpenClaw skills

Configuration (environment variables)
--------------------------------------
  OPENCLAW_BASE_URL   – Base URL of the OpenClaw runtime, e.g.
                        "http://openclaw-host:8080".
                        No default; endpoint returns 503 when unset.
  OPENCLAW_API_KEY    – Optional Bearer token sent to OpenClaw.
  OPENCLAW_TIMEOUT    – HTTP timeout in seconds (default: 120).

Endpoints
---------
  POST /openclaw/chat
      Generic chat: forwards a user message plus session context to OpenClaw.

  POST /openclaw/skills/audio-summary
      Deterministic shortcut: calls the ``smart_classroom_audio_summary``
      skill directly.

  POST /openclaw/skills/mindmap
      Deterministic shortcut: calls the ``smart_classroom_mindmap`` skill
      directly.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

openclaw_router = APIRouter(prefix="/openclaw", tags=["OpenClaw"])

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _get_base_url() -> str:
    """Return the configured OpenClaw base URL or raise 503."""
    url = os.environ.get("OPENCLAW_BASE_URL", "").rstrip("/")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "OpenClaw integration is not configured. "
                "Set the OPENCLAW_BASE_URL environment variable."
            ),
        )
    return url


def _build_headers() -> Dict[str, str]:
    """Build HTTP headers for OpenClaw requests."""
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.environ.get("OPENCLAW_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _get_timeout() -> float:
    """Return the configured HTTP timeout (seconds)."""
    try:
        return float(os.environ.get("OPENCLAW_TIMEOUT", "120"))
    except ValueError:
        return 120.0


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------

async def _call_openclaw(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST *payload* to the OpenClaw ``/invoke`` endpoint.

    Parameters
    ----------
    payload:
        The JSON body forwarded to OpenClaw.  Must at minimum contain a
        ``"skill"`` key with the skill name.

    Returns
    -------
    dict
        The parsed JSON response from OpenClaw.

    Raises
    ------
    HTTPException
        503 – OPENCLAW_BASE_URL not configured.
        502 – Network / connection error reaching OpenClaw.
        502 – Non-2xx HTTP response from OpenClaw.
        502 – OpenClaw returned an unexpected / non-JSON response.
    """
    base_url = _get_base_url()
    invoke_url = f"{base_url}/invoke"
    headers = _build_headers()
    timeout = _get_timeout()

    logger.info(
        "OpenClaw proxy → %s  skill=%s  session_id=%s",
        invoke_url,
        payload.get("skill"),
        payload.get("input", {}).get("session_id", "<none>"),
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(invoke_url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        logger.error("OpenClaw request timed out: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenClaw request timed out after {timeout}s.",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("OpenClaw network error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach OpenClaw at {base_url}: {exc}",
        ) from exc

    if not resp.is_success:
        logger.error(
            "OpenClaw returned HTTP %s: %s", resp.status_code, resp.text[:500]
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenClaw returned HTTP {resp.status_code}: {resp.text[:500]}",
        )

    try:
        return resp.json()
    except Exception as exc:
        logger.error("OpenClaw returned non-JSON body: %s", resp.text[:500])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenClaw returned a non-JSON response.",
        ) from exc


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class OpenClawChatRequest(BaseModel):
    """Request body for the generic chat endpoint."""

    message: str = Field(..., description="Natural language message from the user.")
    session_id: Optional[str] = Field(
        None, description="Active Smart Classroom session ID."
    )
    audio_filename: Optional[str] = Field(
        None, description="Name of the uploaded audio file for context."
    )
    summary_markdown: Optional[str] = Field(
        None, description="Current classroom summary text for context."
    )


class OpenClawAudioSummaryRequest(BaseModel):
    """Request body for the audio-summary skill shortcut."""

    session_id: Optional[str] = Field(None)
    audio_file: Optional[str] = Field(
        None, description="Absolute path to an audio file on the server."
    )
    audio_filename: Optional[str] = Field(
        None,
        description="Filename of audio already staged via /upload-audio.",
    )
    include_transcript: bool = Field(True)


class OpenClawMindmapRequest(BaseModel):
    """Request body for the mindmap skill shortcut."""

    session_id: Optional[str] = Field(None)
    summary_markdown: Optional[str] = Field(None)
    language: str = Field("en")
    output_format: str = Field("jsmind_json")
    include_raw: bool = Field(False)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@openclaw_router.post("/chat")
async def openclaw_chat(request: OpenClawChatRequest) -> Dict[str, Any]:
    """Generic chat endpoint.

    Forwards the user's message and available session context to OpenClaw.
    OpenClaw selects the most appropriate skill or responds conversationally.

    The ``message`` field is forwarded as ``input.message``.  If a
    ``session_id``, ``audio_filename``, or ``summary_markdown`` is provided
    they are included in ``input`` so OpenClaw skills have access to session
    artefacts.

    Example request::

        POST /openclaw/chat
        {
          "message": "Summarize the current classroom audio and generate a mindmap.",
          "session_id": "20260509-120000-ab12",
          "audio_filename": "lecture.mp3"
        }
    """
    skill_input: Dict[str, Any] = {"message": request.message}
    if request.session_id:
        skill_input["session_id"] = request.session_id
    if request.audio_filename:
        skill_input["audio_filename"] = request.audio_filename
    if request.summary_markdown:
        skill_input["summary_markdown"] = request.summary_markdown

    payload = {"skill": "chat", "input": skill_input}
    return await _call_openclaw(payload)


@openclaw_router.post("/skills/audio-summary")
async def openclaw_audio_summary(
    request: OpenClawAudioSummaryRequest,
) -> Dict[str, Any]:
    """Invoke the ``smart_classroom_audio_summary`` skill deterministically.

    Either ``audio_file`` (server-side absolute path) or ``audio_filename``
    (file staged via ``/upload-audio``) must be provided.

    Example request::

        POST /openclaw/skills/audio-summary
        {
          "session_id": "20260509-120000-ab12",
          "audio_filename": "lecture.mp3",
          "include_transcript": true
        }

    The response mirrors the skill's output schema::

        {
          "session_id": "...",
          "summary_markdown": "## Summary\\n...",
          "transcript": "TEACHER: ...",
          "transcription_events": [...]
        }
    """
    if not request.audio_file and not request.audio_filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'audio_file' or 'audio_filename' must be provided.",
        )

    skill_input: Dict[str, Any] = {
        "include_transcript": request.include_transcript,
    }
    if request.session_id:
        skill_input["session_id"] = request.session_id
    if request.audio_file:
        skill_input["audio_file"] = request.audio_file
    if request.audio_filename:
        skill_input["audio_filename"] = request.audio_filename

    payload = {"skill": "smart_classroom_audio_summary", "input": skill_input}
    return await _call_openclaw(payload)


@openclaw_router.post("/skills/mindmap")
async def openclaw_mindmap(
    request: OpenClawMindmapRequest,
) -> Dict[str, Any]:
    """Invoke the ``smart_classroom_mindmap`` skill deterministically.

    Either ``session_id`` or ``summary_markdown`` must be provided.
    Both can be supplied: when ``summary_markdown`` is given it is written
    into the session directory so the pipeline can read it.

    Typical chained call after ``/openclaw/skills/audio-summary``::

        POST /openclaw/skills/mindmap
        {
          "session_id": "<audio_summary.session_id>",
          "summary_markdown": "<audio_summary.summary_markdown>",
          "output_format": "jsmind_json"
        }

    The response mirrors the skill's output schema::

        {
          "session_id": "...",
          "mindmap": { "meta": {...}, "format": "node_tree", "data": {...} },
          "format": "jsmind_json",
          "source": "summary_markdown"
        }
    """
    if not request.session_id and not request.summary_markdown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either 'session_id' or 'summary_markdown' must be provided.",
        )

    skill_input: Dict[str, Any] = {
        "language": request.language,
        "output_format": request.output_format,
        "include_raw": request.include_raw,
    }
    if request.session_id:
        skill_input["session_id"] = request.session_id
    if request.summary_markdown:
        skill_input["summary_markdown"] = request.summary_markdown

    payload = {"skill": "smart_classroom_mindmap", "input": skill_input}
    return await _call_openclaw(payload)
