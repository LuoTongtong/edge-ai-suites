/*
 * Copyright (C) 2026 Intel Corporation
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Frontend API helpers for the OpenClaw proxy endpoints.
 *
 * The UI calls the Smart Classroom backend proxy, which forwards requests to
 * the configured OpenClaw runtime.  OpenClaw credentials never reach the
 * browser.
 *
 * Endpoints consumed:
 *   POST /openclaw/chat
 *   POST /openclaw/skills/audio-summary
 *   POST /openclaw/skills/mindmap
 */

const env = (import.meta as any).env ?? {};
const BASE_URL: string = env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

// ── Request/response types ────────────────────────────────────────────────

export interface OpenClawChatRequest {
  message: string;
  session_id?: string | null;
  audio_filename?: string | null;
  summary_markdown?: string | null;
}

export interface OpenClawAudioSummaryRequest {
  session_id?: string | null;
  audio_file?: string | null;
  audio_filename?: string | null;
  include_transcript?: boolean;
}

export interface OpenClawMindmapRequest {
  session_id?: string | null;
  summary_markdown?: string | null;
  language?: string;
  output_format?: 'jsmind_json' | 'raw';
  include_raw?: boolean;
}

export interface OpenClawAudioSummaryResult {
  session_id: string;
  summary_markdown: string;
  transcript?: string | null;
  transcription_events?: unknown[];
}

export interface OpenClawMindmapResult {
  session_id: string;
  mindmap: unknown | null;
  raw_mindmap?: string;
  format: string;
  source: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────

async function _postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      detail = err.detail || err.message || detail;
    } catch {
      // ignore JSON parse error
    }
    throw new Error(detail);
  }

  return res.json() as Promise<T>;
}

// ── Public API functions ──────────────────────────────────────────────────

/**
 * Send a natural-language message to OpenClaw via the backend proxy.
 *
 * @param req  Chat request including message and optional session context.
 * @returns    Parsed JSON response from OpenClaw.
 */
export async function openclawChat(req: OpenClawChatRequest): Promise<unknown> {
  return _postJson('/openclaw/chat', req);
}

/**
 * Invoke the `smart_classroom_audio_summary` skill deterministically.
 *
 * Either `audio_file` or `audio_filename` must be provided.
 */
export async function openclawAudioSummary(
  req: OpenClawAudioSummaryRequest,
): Promise<OpenClawAudioSummaryResult> {
  return _postJson('/openclaw/skills/audio-summary', req);
}

/**
 * Invoke the `smart_classroom_mindmap` skill deterministically.
 *
 * Either `session_id` or `summary_markdown` must be provided.
 */
export async function openclawMindmap(
  req: OpenClawMindmapRequest,
): Promise<OpenClawMindmapResult> {
  return _postJson('/openclaw/skills/mindmap', req);
}
