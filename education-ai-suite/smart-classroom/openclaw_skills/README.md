# Smart Classroom — OpenClaw Audio Summary Skill

This package exposes the Smart Classroom **audio summary** capability as an
OpenClaw-callable skill.  The skill adapter invokes the existing `Pipeline`
directly (no running FastAPI server is required).

---

## Directory structure

```
education-ai-suite/smart-classroom/
└── openclaw_skills/
    ├── __init__.py            # Package entry point
    ├── audio_summary.py       # Skill adapter + convenience function
    ├── skill_manifest.json    # OpenClaw skill schema / metadata
    ├── README.md              # This file
    └── tests/
        ├── __init__.py
        └── test_audio_summary.py   # Lightweight unit tests (no real models)
```

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

The skill **must** be invoked with the working directory set to
`education-ai-suite/smart-classroom/` so that relative imports and config
file discovery work correctly.

```bash
cd education-ai-suite/smart-classroom
```

---

## Quick start — Python

```python
from openclaw_skills.audio_summary import run_audio_summary

result = run_audio_summary(audio_file="/path/to/lecture.wav")

print(result["session_id"])        # e.g. "20240101-120000-ab12"
print(result["summary_markdown"])  # full Markdown summary
print(result["transcript"])        # full transcript text
```

### Using a pre-staged audio file

If the audio was already uploaded via the FastAPI `/upload-audio` endpoint,
pass its filename rather than the full path:

```python
result = run_audio_summary(audio_filename="lecture.wav")
```

The skill resolves the file against the configured project storage directory
(`{Project.location}/{Project.name}/audio/{audio_filename}`).

### Reusing an existing session

```python
result = run_audio_summary(
    audio_file="/path/to/lecture.wav",
    session_id="20240101-120000-ab12",   # must already exist
)
```

### Omitting the transcript from the output

```python
result = run_audio_summary(
    audio_file="/path/to/lecture.wav",
    include_transcript=False,
)
# result["transcript"] is absent from the output dict
```

---

## OpenClaw invocation

Register the skill in OpenClaw using the metadata from `skill_manifest.json`:

```json
{
  "name": "smart_classroom_audio_summary",
  "entry_point": {
    "module": "openclaw_skills.audio_summary",
    "function": "run_audio_summary"
  }
}
```

Then call it as:

```json
{
  "skill": "smart_classroom_audio_summary",
  "input": {
    "audio_file": "/data/classroom/lecture.wav"
  }
}
```

---

## Input schema

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `audio_file` | string | one of | — | Full path to an audio file |
| `audio_filename` | string | one of | — | Pre-staged filename (relative to project storage) |
| `session_id` | string | no | auto-generated | Reuse an existing session |
| `source_type` | string | no | `"audio_file"` | Only `"audio_file"` is supported |
| `include_transcript` | boolean | no | `true` | Include transcript text in output |

At least one of `audio_file` or `audio_filename` must be provided.

---

## Output schema

| Field | Type | Description |
|---|---|---|
| `session_id` | string | Session id used for this run |
| `summary_markdown` | string | Full generated summary (Markdown) |
| `transcript` | string \| null | Full transcript (when `include_transcript=true`) |
| `transcription_events` | array | Raw chunk-level events from the pipeline |

---

## Error handling

| Exception | Cause |
|---|---|
| `AudioSummaryInputError` | Missing/invalid input (no audio, file not found, unsupported `source_type`) |
| `RuntimeError` | Pipeline init, transcription, or summarisation failure |

Both exception types carry a descriptive message.

---

## Running the tests

```bash
# From the smart-classroom directory:
cd education-ai-suite/smart-classroom
python -m pytest openclaw_skills/tests/ -v
```

The tests use `unittest.mock` to patch `Pipeline` and config utilities so
**no real ASR or LLM models are loaded**.

---

## Artifacts written to disk

The pipeline writes the following files under
`{Project.location}/{Project.name}/{session_id}/`:

| File | Description |
|---|---|
| `transcription.txt` | Full transcript (speaker-labelled lines) |
| `teacher_transcription.txt` | Teacher-only lines (when diarisation is enabled) |
| `summary.md` | Generated Markdown summary |
| `performance_metrics.csv` | Timing / throughput metrics |
