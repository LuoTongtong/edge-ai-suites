# OpenClaw Integration for Smart Classroom

This document explains how to configure and use the OpenClaw assistant panel
that is embedded in the Smart Classroom UI.

## Architecture

```
Smart Classroom UI
  └─▶ Smart Classroom backend proxy  (this repo, FastAPI)
        └─▶ OpenClaw runtime          (external service, OPENCLAW_BASE_URL)
              └─▶ Smart Classroom OpenClaw skills
                    ├─ smart_classroom_audio_summary
                    └─ smart_classroom_mindmap
```

OpenClaw credentials never reach the browser — the UI only calls the Smart
Classroom backend, which forwards requests to OpenClaw using server-side
environment variables.

---

## Backend configuration

### Environment variables

| Variable           | Required | Default | Description |
|--------------------|----------|---------|-------------|
| `OPENCLAW_BASE_URL` | **Yes** | *(none)* | Base URL of the OpenClaw runtime, e.g. `http://openclaw-host:8080`. The proxy returns **503** when this is unset. |
| `OPENCLAW_API_KEY`  | No | *(none)* | Bearer token sent as `Authorization: Bearer <token>` to OpenClaw. Omit if OpenClaw does not require authentication. |
| `OPENCLAW_TIMEOUT`  | No | `120` | HTTP timeout (seconds) for calls to OpenClaw. Increase for long audio files. |

### Example `.env`

```bash
OPENCLAW_BASE_URL=http://localhost:9999
OPENCLAW_API_KEY=my-secret-token
OPENCLAW_TIMEOUT=180
```

### OpenClaw skill registration

Ensure that the following skills are registered in the OpenClaw runtime
before using the integration:

| Skill name | Source |
|---|---|
| `smart_classroom_audio_summary` | `openclaw_skills/audio_summary.py` |
| `smart_classroom_mindmap` | `openclaw_skills/mindmap/skill.py` |

The skills invoke the existing Smart Classroom `Pipeline` directly, so they
must run with:

- PYTHONPATH pointing to the `education-ai-suite/smart-classroom` directory.
- `config.yaml` and `runtime_config.yaml` accessible.
- ASR and LLM models available as configured in `config.yaml`.
- `ffmpeg` on `PATH`.

### Shared file paths

When OpenClaw runs in a separate container or process from the Smart
Classroom backend, the audio files and session artefacts (stored under the
`Project.location` / `Project.name` directory tree) must be accessible to
both. Use a shared volume or NFS mount.

---

## Proxy endpoints

The Smart Classroom backend exposes three new endpoints under the
`/openclaw/` prefix.

### `POST /openclaw/chat`

Generic chat endpoint. Forwards a natural-language message and optional
session context to OpenClaw. OpenClaw chooses the skill to invoke.

**Request body**

```json
{
  "message": "Summarize the current classroom audio and generate a mindmap.",
  "session_id": "20260509-120000-ab12",
  "audio_filename": "lecture.mp3",
  "summary_markdown": "## Existing summary\n..."
}
```

All fields except `message` are optional.

**Example (curl)**

```bash
curl -X POST http://localhost:8000/openclaw/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "Summarize the current classroom audio and generate a mindmap.",
    "session_id": "20260509-120000-ab12",
    "audio_filename": "lecture.mp3"
  }'
```

---

### `POST /openclaw/skills/audio-summary`

Calls `smart_classroom_audio_summary` deterministically.

**Request body**

```json
{
  "session_id": "20260509-120000-ab12",
  "audio_filename": "lecture.mp3",
  "include_transcript": true
}
```

Either `audio_file` (absolute server-side path) or `audio_filename`
(filename staged via `/upload-audio`) must be provided.

**Example (curl)**

```bash
curl -X POST http://localhost:8000/openclaw/skills/audio-summary \
  -H 'Content-Type: application/json' \
  -d '{
    "audio_filename": "lecture.mp3",
    "include_transcript": true
  }'
```

**Response**

```json
{
  "session_id": "20260509-120000-ab12",
  "summary_markdown": "## Teacher Summary\n- ...\n\n## Key Takeaways\n- ...",
  "transcript": "TEACHER: Good morning class...",
  "transcription_events": [...]
}
```

---

### `POST /openclaw/skills/mindmap`

Calls `smart_classroom_mindmap` deterministically. Can be chained after
`/openclaw/skills/audio-summary` using the returned `session_id` and
`summary_markdown`.

**Request body**

```json
{
  "session_id": "20260509-120000-ab12",
  "summary_markdown": "## Summary\n- Topic A\n- Topic B",
  "output_format": "jsmind_json",
  "language": "en"
}
```

Either `session_id` or `summary_markdown` must be provided.

**Example (curl)**

```bash
curl -X POST http://localhost:8000/openclaw/skills/mindmap \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "20260509-120000-ab12",
    "summary_markdown": "## Summary\n- Topic A\n- Topic B",
    "output_format": "jsmind_json"
  }'
```

**Response**

```json
{
  "session_id": "20260509-120000-ab12",
  "mindmap": {
    "meta": { "name": "Class MindMap", "author": "ai_assistant", "version": "1.0" },
    "format": "node_tree",
    "data": {
      "id": "root",
      "topic": "Class Summary",
      "children": [
        { "id": "1", "topic": "Topic A", "children": [] },
        { "id": "2", "topic": "Topic B", "children": [] }
      ]
    }
  },
  "format": "jsmind_json",
  "source": "summary_markdown"
}
```

---

## Chaining skills example

The following Python snippet shows how to call audio summary and then
mindmap in sequence:

```python
import requests

BASE = "http://localhost:8000"

# Step 1: audio summary
summary_resp = requests.post(f"{BASE}/openclaw/skills/audio-summary", json={
    "audio_filename": "lecture.mp3",
    "include_transcript": False,
}).json()

session_id = summary_resp["session_id"]
summary_markdown = summary_resp["summary_markdown"]

# Step 2: mindmap chained from summary
mindmap_resp = requests.post(f"{BASE}/openclaw/skills/mindmap", json={
    "session_id": session_id,
    "summary_markdown": summary_markdown,
    "output_format": "jsmind_json",
}).json()

print(mindmap_resp["mindmap"])
```

---

## UI usage

When the Smart Classroom UI is running, a **🤖** button appears in the
bottom-right corner of the screen. Clicking it opens the OpenClaw assistant
panel with:

- **Quick-action buttons** for deterministic skill invocations:
  - 🎙️ **Audio Summary** — calls `/openclaw/skills/audio-summary`
  - 🗺️ **MindMap** — calls `/openclaw/skills/mindmap`
  - ⚡ **Summary + MindMap** — chains both skills
- **Free-text chat** — calls `/openclaw/chat` for conversational requests.

The panel automatically includes the current `session_id` and uploaded
audio filename from the active Smart Classroom session, so the user does
not need to provide them manually.

---

## Error codes

| HTTP status | Meaning |
|-------------|---------|
| 422 | Missing required input (e.g. no audio provided). |
| 503 | `OPENCLAW_BASE_URL` is not configured. |
| 502 | Network error, timeout, or non-2xx response from OpenClaw. |

---

## Note on synchronous operation

The first version of the proxy is **synchronous**: the HTTP request blocks
until OpenClaw completes the skill. For long audio files this may take
several minutes. Adjust `OPENCLAW_TIMEOUT` accordingly (default: 120 s).
An async job / SSE streaming implementation can be added in a future
iteration without breaking the current API contract.
