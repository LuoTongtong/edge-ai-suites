/*
 * Copyright (C) 2026 Intel Corporation
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * OpenClawPanel – floating assistant chat panel for the Smart Classroom UI.
 *
 * Features
 * --------
 * - Floating action button (bottom-right) that opens/closes the panel.
 * - Quick-action buttons for deterministic skill invocations:
 *     • "Audio Summary" → POST /openclaw/skills/audio-summary
 *     • "MindMap" → POST /openclaw/skills/mindmap
 *     • "Summary + MindMap" → audio-summary then mindmap chained
 * - Free-text chat → POST /openclaw/chat
 * - Shows loading state (animated dots) while waiting for OpenClaw.
 * - Displays skill results (summary_markdown, mindmap JSON) inline.
 * - Reads session_id and uploadedAudioPath from Redux store to provide
 *   context without the user having to type it.
 * - Never exposes OpenClaw credentials (all calls go through the backend).
 */

import React, { useEffect, useRef, useState } from 'react';
import { useAppSelector } from '../../redux/hooks';
import {
  openclawChat,
  openclawAudioSummary,
  openclawMindmap,
} from '../../services/openclaw';
import './OpenClawPanel.css';

// ── Message model ─────────────────────────────────────────────────────────

type MessageRole = 'user' | 'assistant' | 'error';

interface Message {
  id: number;
  role: MessageRole;
  text: string;
  /** Structured result attached to an assistant message, if any. */
  result?: Record<string, unknown>;
}

let _msgIdCounter = 0;
const nextId = () => ++_msgIdCounter;

// ── Component ─────────────────────────────────────────────────────────────

const OpenClawPanel: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // Read context from Redux store
  const sessionId = useAppSelector((s) => s.ui.sessionId);
  const uploadedAudioPath = useAppSelector((s) => s.ui.uploadedAudioPath);
  const summaryFinalText = useAppSelector((s) => s.summary.finalText);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  // Focus textarea when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // ── Helpers ──────────────────────────────────────────────────────────

  const addMessage = (role: MessageRole, text: string, result?: Record<string, unknown>) =>
    setMessages((prev) => [...prev, { id: nextId(), role, text, result }]);

  const handleError = (err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err);
    addMessage('error', `OpenClaw error: ${msg}`);
  };

  /** Extract the audio filename from the staged path stored in Redux. */
  const getAudioFilename = (): string | null => {
    if (!uploadedAudioPath) return null;
    if (uploadedAudioPath === 'MICROPHONE') return null;
    // uploadedAudioPath may be a filename or a full path
    return uploadedAudioPath.split('/').pop() ?? uploadedAudioPath;
  };

  // ── Action handlers ───────────────────────────────────────────────────

  const handleChat = async () => {
    const text = inputText.trim();
    if (!text || isLoading) return;

    setInputText('');
    addMessage('user', text);
    setIsLoading(true);

    try {
      const resp = await openclawChat({
        message: text,
        session_id: sessionId,
        audio_filename: getAudioFilename(),
        summary_markdown: summaryFinalText ?? undefined,
      });

      // Attempt to extract a human-readable text from the response
      const respObj = resp as Record<string, unknown>;
      const outputObj = respObj?.output as Record<string, unknown> | undefined;
      const replyText =
        (outputObj?.text as string | undefined) ||
        (respObj?.result as string | undefined) ||
        JSON.stringify(resp, null, 2);

      addMessage('assistant', replyText, respObj);
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleAudioSummary = async () => {
    if (isLoading) return;

    const audioFilename = getAudioFilename();
    if (!audioFilename && !sessionId) {
      addMessage(
        'error',
        'No audio file or session available. Upload an audio file or start a session first.',
      );
      return;
    }

    addMessage('user', '🎙️ Generate Audio Summary with OpenClaw');
    setIsLoading(true);

    try {
      const result = await openclawAudioSummary({
        session_id: sessionId,
        audio_filename: audioFilename ?? undefined,
        include_transcript: true,
      });

      const summary = result.summary_markdown || '(no summary returned)';
      addMessage(
        'assistant',
        `✅ Audio summary complete (session: ${result.session_id})\n\n${summary.slice(0, 400)}${summary.length > 400 ? '…' : ''}`,
        result as unknown as Record<string, unknown>,
      );
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleMindmap = async () => {
    if (isLoading) return;

    if (!sessionId && !summaryFinalText) {
      addMessage(
        'error',
        'No session or summary available. Run an audio summary first.',
      );
      return;
    }

    addMessage('user', '🗺️ Generate MindMap with OpenClaw');
    setIsLoading(true);

    try {
      const result = await openclawMindmap({
        session_id: sessionId,
        summary_markdown: summaryFinalText ?? undefined,
        output_format: 'jsmind_json',
      });

      const mindmapLabel = result.mindmap
        ? 'jsMind JSON mindmap generated'
        : result.raw_mindmap
          ? 'MindMap text generated (could not parse as JSON)'
          : '(no mindmap data returned)';

      addMessage(
        'assistant',
        `✅ MindMap complete (session: ${result.session_id})\n${mindmapLabel}`,
        result as unknown as Record<string, unknown>,
      );
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSummaryAndMindmap = async () => {
    if (isLoading) return;

    const audioFilename = getAudioFilename();
    if (!audioFilename && !sessionId) {
      addMessage(
        'error',
        'No audio file or session available. Upload an audio file or start a session first.',
      );
      return;
    }

    addMessage('user', '⚡ Generate Summary + MindMap with OpenClaw');
    setIsLoading(true);

    try {
      // Step 1: audio summary
      const summaryResult = await openclawAudioSummary({
        session_id: sessionId,
        audio_filename: audioFilename ?? undefined,
        include_transcript: false,
      });

      const summaryText = summaryResult.summary_markdown || '';
      addMessage(
        'assistant',
        `✅ Audio summary done (session: ${summaryResult.session_id})\n\n${summaryText.slice(0, 300)}${summaryText.length > 300 ? '…' : ''}`,
        summaryResult as unknown as Record<string, unknown>,
      );

      // Step 2: mindmap chained from summary
      const mindmapResult = await openclawMindmap({
        session_id: summaryResult.session_id,
        summary_markdown: summaryText,
        output_format: 'jsmind_json',
      });

      const mindmapLabel = mindmapResult.mindmap
        ? 'jsMind JSON mindmap generated'
        : 'MindMap text generated';

      addMessage(
        'assistant',
        `✅ MindMap done (session: ${mindmapResult.session_id})\n${mindmapLabel}`,
        mindmapResult as unknown as Record<string, unknown>,
      );
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleChat();
    }
  };

  // ── Result rendering ──────────────────────────────────────────────────

  const renderResult = (result: Record<string, unknown>) => {
    if (!result || typeof result !== 'object') return null;

    const hasSummary = typeof result.summary_markdown === 'string' && (result.summary_markdown as string).length > 0;
    const hasMindmap = result.mindmap != null;

    if (!hasSummary && !hasMindmap) return null;

    return (
      <div className="openclaw-result-section">
        {hasSummary && (
          <>
            <h4>Summary (truncated)</h4>
            <pre>{(result.summary_markdown as string).slice(0, 200)}</pre>
          </>
        )}
        {hasMindmap && (
          <>
            <h4>MindMap JSON (truncated)</h4>
            <pre>{JSON.stringify(result.mindmap, null, 2).slice(0, 200)}</pre>
          </>
        )}
      </div>
    );
  };

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <>
      {/* Floating action button */}
      <button
        className="openclaw-fab"
        onClick={() => setIsOpen((v) => !v)}
        title="OpenClaw Assistant"
        aria-label="Open OpenClaw assistant"
      >
        🤖
      </button>

      {/* Slide-in panel */}
      {isOpen && (
        <div className="openclaw-panel" role="dialog" aria-label="OpenClaw Assistant">
          {/* Header */}
          <div className="openclaw-panel-header">
            <span className="openclaw-panel-header-title">
              🤖 OpenClaw Assistant
            </span>
            <button
              className="openclaw-panel-close"
              onClick={() => setIsOpen(false)}
              aria-label="Close"
            >
              ×
            </button>
          </div>

          {/* Quick-action buttons */}
          <div className="openclaw-actions">
            <button
              className="openclaw-action-btn"
              onClick={handleAudioSummary}
              disabled={isLoading}
              title="Invoke smart_classroom_audio_summary skill"
            >
              🎙️ Audio Summary
            </button>
            <button
              className="openclaw-action-btn"
              onClick={handleMindmap}
              disabled={isLoading}
              title="Invoke smart_classroom_mindmap skill"
            >
              🗺️ MindMap
            </button>
            <button
              className="openclaw-action-btn"
              onClick={handleSummaryAndMindmap}
              disabled={isLoading}
              title="Audio summary then mindmap (chained)"
            >
              ⚡ Summary + MindMap
            </button>
          </div>

          {/* Message list */}
          <div className="openclaw-messages">
            {messages.length === 0 && !isLoading && (
              <div className="openclaw-message assistant">
                <div className="openclaw-bubble">
                  Hi! I'm the OpenClaw assistant. Use the buttons above for
                  quick actions, or type a message to chat.
                </div>
              </div>
            )}

            {messages.map((msg) => (
              <div key={msg.id} className={`openclaw-message ${msg.role}`}>
                <div className="openclaw-bubble">{msg.text}</div>
                {msg.result && renderResult(msg.result)}
              </div>
            ))}

            {isLoading && (
              <div className="openclaw-message assistant">
                <div className="openclaw-bubble">
                  <div className="openclaw-typing">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input row */}
          <div className="openclaw-input-row">
            <textarea
              ref={textareaRef}
              className="openclaw-textarea"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask OpenClaw anything… (Enter to send)"
              rows={1}
              disabled={isLoading}
            />
            <button
              className="openclaw-send-btn"
              onClick={handleChat}
              disabled={isLoading || !inputText.trim()}
              title="Send"
              aria-label="Send message"
            >
              ➤
            </button>
          </div>
        </div>
      )}
    </>
  );
};

export default OpenClawPanel;
