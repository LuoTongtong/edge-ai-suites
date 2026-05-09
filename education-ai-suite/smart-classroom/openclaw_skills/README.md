# Smart Classroom — OpenClaw Skills

This package exposes Smart Classroom capabilities as OpenClaw-callable skills.
The skill adapters invoke the existing `Pipeline` directly — no running FastAPI
server is required.

---

## Directory structure

```
education-ai-suite/smart-classroom/
└── openclaw_skills/
    ├── __init__.py            # Package entry point
    ├── audio_summary.py       # Audio summary skill adapter
    ├── skill_manifest.json    # OpenClaw skill schema / metadata
    ├── README.md              # This file
    ├── mindmap/
    │   ├── __init__.py
    │   ├── skill.py           # MindMap skill adapter
    │   └── manifest.json      # MindMap skill schema / metadata
    └── tests/
        ├── __init__.py
        ├── test_audio_summary.py    # Audio summary unit tests (no real models)
        └── test_mindmap_skill.py    # MindMap unit tests (no real models)
```

---

## Available skills

| Skill | Entry point | Description |
|---|---|---|
| `smart_classroom_audio_summary` | `openclaw_skills.audio_summary.run_audio_summary` | Transcribe audio → generate Markdown summary |
| `smart_classroom_mindmap` | `openclaw_skills.mindmap.skill.run` | Generate jsMind JSON mind map from a classroom summary |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python ≥ 3.9 | Match the environment used by Smart Classroom |
| `ffmpeg` on PATH | Required by the audio chunking component |
| Smart Classroom dependencies installed | `pip install -r requirements.txt` from the `smart-classroom` directory |
| `config.yaml` and `runtime_config.yaml` present | Controls model selection, storage paths, etc. |
| ASR and LLM models available | As configured in `config.yaml` |

### Working directory

Both skills **must** be invoked with the working directory set to
`education-ai-suite/smart-classroom/` so that relative imports and config
file discovery work correctly.

```bash
cd education-ai-suite/smart-classroom
```

---

## Skill: `smart_classroom_audio_summary`

Transcribes classroom audio and generates a structured Markdown summary.

### Quick start

```python
from openclaw_skills.audio_summary import run_audio_summary

result = run_audio_summary(audio_file="/path/to/lecture.wav")

print(result["session_id"])        # e.g. "20240101-120000-ab12"
print(result["summary_markdown"])  # full Markdown summary
print(result["transcript"])        # full transcript text
```

### Using a pre-staged audio file

```python
result = run_audio_summary(audio_filename="lecture.wav")
```

The skill resolves the file against the configured project storage directory
(`{Project.location}/{Project.name}/audio/{audio_filename}`).

### Reusing an existing session

```python
result = run_audio_summary(
    audio_file="/path/to/lecture.wav",
    session_id="20240101-120000-ab12",
)
```

### OpenClaw invocation

Register the skill in OpenClaw using the metadata from `skill_manifest.json`:

```json
{
  "skill": "smart_classroom_audio_summary",
  "input": {
    "audio_file": "/data/classroom/lecture.wav"
  }
}
```

### Input schema

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `audio_file` | string | one of | — | Full path to an audio file |
| `audio_filename` | string | one of | — | Pre-staged filename (relative to project storage) |
| `session_id` | string | no | auto-generated | Reuse an existing session |
| `source_type` | string | no | `"audio_file"` | Only `"audio_file"` is supported |
| `include_transcript` | boolean | no | `true` | Include transcript text in output |

At least one of `audio_file` or `audio_filename` must be provided.

### Output schema

| Field | Type | Description |
|---|---|---|
| `session_id` | string | Session id used for this run |
| `summary_markdown` | string | Full generated summary (Markdown) |
| `transcript` | string \| null | Full transcript (when `include_transcript=true`) |
| `transcription_events` | array | Raw chunk-level events from the pipeline |

---

## Skill: `smart_classroom_mindmap`

Generates a jsMind-compatible JSON mind map from a classroom summary. Accepts either
an existing `session_id` (reads `summary.md` from the session directory) or a
`summary_markdown` string supplied directly (e.g. the output of
`smart_classroom_audio_summary`).

> **Format note:** The LLM prompt in `config.yaml` instructs the model to produce
> **jsMind JSON** (with `meta`, `format`, and `data` fields), not Mermaid.
> `output_format` defaults to `"jsmind_json"`.

### Quick start

```python
from openclaw_skills.mindmap.skill import run as run_mindmap

result = run_mindmap({
    "session_id": "20240101-120000-ab12",
    "summary_markdown": "## Teacher Summary\n- Wave-particle duality ...",
    "output_format": "jsmind_json",
})

print(result["mindmap"])      # parsed jsMind JSON dict
print(result["format"])       # "jsmind_json"
print(result["source"])       # "summary_markdown"
```

### OpenClaw invocation

```json
{
  "skill": "smart_classroom_mindmap",
  "input": {
    "session_id": "20260509-143022-ab12",
    "summary_markdown": "## Teacher Summary\n- ...",
    "output_format": "jsmind_json"
  }
}
```

### Input schema

| Field | Type | Required | Description |
|---|---|---|---|
| `session_id` | string | one of | Existing session ID |
| `summary_markdown` | string | one of | Markdown summary text |
| `language` | string | no | `"en"` (default) or `"zh"` |
| `output_format` | string | no | `"jsmind_json"` (default) or `"raw"` |
| `include_raw` | bool | no | Also return raw LLM string (default `false`) |

At least one of `session_id` or `summary_markdown` must be provided.

### Output schema

| Field | Type | Description |
|---|---|---|
| `session_id` | string | Session used / created for this request |
| `mindmap` | object \| null | Parsed jsMind JSON; `null` when parsing failed |
| `raw_mindmap` | string | Raw LLM output (present when `include_raw=true` or parsing failed) |
| `format` | string | `"jsmind_json"` or `"raw"` |
| `source` | string | `"session_id"` or `"summary_markdown"` |

---

## Chaining: Audio Summary → MindMap

The two skills are designed to chain: pass `session_id` and `summary_markdown`
from the audio summary output directly into the mindmap skill.

### Python

```python
from openclaw_skills.audio_summary import run_audio_summary
from openclaw_skills.mindmap.skill import run as run_mindmap

# Step 1 — transcribe and summarise
audio_result = run_audio_summary(audio_file="/data/classes/physics_lecture.mp3")
# → {"session_id": "20260509-...", "summary_markdown": "## Teacher Summary\n..."}

# Step 2 — generate mind map (pass both fields directly)
mindmap_result = run_mindmap({
    "session_id":       audio_result["session_id"],
    "summary_markdown": audio_result["summary_markdown"],
    "output_format":    "jsmind_json",
})
# → {"mindmap": {"meta": {...}, "format": "node_tree", "data": {...}}, ...}
```

### OpenClaw orchestration

```json
{
  "skill": "smart_classroom_audio_summary",
  "input": {"audio_file": "/data/classes/physics_lecture.mp3", "include_transcript": true}
}
```

Then pass `summary_markdown` and `session_id` into:

```json
{
  "skill": "smart_classroom_mindmap",
  "input": {
    "session_id": "20260509-143022-ab12",
    "summary_markdown": "## Teacher Summary\n- Wave-particle duality ...",
    "output_format": "jsmind_json"
  }
}
```

---

## Behavior notes

- **Minimum token check:** `Pipeline.run_mindmap()` enforces `mindmap.min_token` from
  `config.yaml` (default: 20 tokens). Short summaries return an `insufficient_input`
  jsMind JSON structure; the skill parses and returns it normally.

- **`summary_markdown` overwrites session summary:** When both `session_id` and
  `summary_markdown` are provided, the supplied markdown is written to that session's
  `summary.md` before running the pipeline, allowing regeneration without a new session.

- **JSON parsing:** The skill strips markdown code fences before attempting to parse
  the LLM output as JSON. On failure, `mindmap` is `null`, `raw_mindmap` is included,
  and `format` is `"raw"`.

---

## Error handling

| Exception | Cause |
|---|---|
| `AudioSummaryInputError` | Missing/invalid input for audio summary (no audio, file not found) |
| `ValueError` | Missing/invalid input for mindmap skill |
| `RuntimeError` | Pipeline init, transcription, or summarisation failure |

---

## Artifacts written to disk

The pipeline writes the following files under
`{Project.location}/{Project.name}/{session_id}/`:

| File | Description |
|---|---|
| `transcription.txt` | Full transcript (speaker-labelled lines) |
| `summary.md` | Generated Markdown summary |
| `mindmap.mmd` | Generated jsMind JSON mind map |
| `performance_metrics.csv` | Timing / throughput metrics |

---

## Running the tests

```bash
# From the smart-classroom directory:
cd education-ai-suite/smart-classroom
python -m pytest openclaw_skills/tests/ -v
```

The tests use `unittest.mock` to patch `Pipeline` and config utilities so
**no real ASR or LLM models are loaded**.
