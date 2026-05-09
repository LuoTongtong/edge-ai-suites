# Smart Classroom OpenClaw Skills

This directory contains [OpenClaw](https://github.com/open-edge-platform/openclaw)-callable skill adapters for the **Smart Classroom** application.

## Available Skills

| Skill | Module | Description |
|---|---|---|
| `smart_classroom_audio_summary` | `openclaw_skills.audio_summary.skill` | Transcribe classroom audio → generate Markdown summary |
| `smart_classroom_mindmap` | `openclaw_skills.mindmap.skill` | Generate a jsMind-compatible JSON mind map from a classroom summary |

---

## Prerequisites

Both skills reuse the existing Smart Classroom pipeline components (ASR, LLM summarizer, MindmapComponent).  
The OpenClaw runtime environment must satisfy:

1. Python path / working directory set to `education-ai-suite/smart-classroom`
2. Dependencies installed: `pip install -r requirements.txt`
3. `config.yaml` and `runtime_config.yaml` present and configured
4. The ASR model and LLM summarizer model available as configured in `config.yaml`

---

## Skill: `smart_classroom_audio_summary`

Transcribes a classroom audio file and generates a structured Markdown summary.

### Registration (conceptual)

```yaml
skills:
  - name: smart_classroom_audio_summary
    type: python
    module: openclaw_skills.audio_summary.skill
    entrypoint: run
    working_dir: /path/to/edge-ai-suites/education-ai-suite/smart-classroom
```

### Input

```json
{
  "audio_file": "/absolute/path/to/lecture.mp3",
  "source_type": "audio_file",
  "include_transcript": true
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `audio_file` | string | one of `audio_file`/`audio_filename` | Absolute path to the audio file |
| `audio_filename` | string | one of `audio_file`/`audio_filename` | Filename already in the upload directory |
| `session_id` | string | no | Reuse an existing Smart Classroom session |
| `source_type` | string | no | `"audio_file"` (default) or `"microphone"` |
| `include_transcript` | bool | no | Include full transcript in output (default `true`) |

### Output

```json
{
  "session_id": "20260509-143022-ab12",
  "summary_markdown": "## Teacher Summary\n- ...\n\n## Key Takeaways\n- ...",
  "transcript": "TEACHER: Today we'll cover quantum mechanics ...",
  "source": "audio_file"
}
```

---

## Skill: `smart_classroom_mindmap`

Generates a jsMind-compatible JSON mind map from a classroom summary.

> **Format note:** The LLM prompt in `config.yaml` instructs the model to produce
> **jsMind JSON** (with `meta`, `format`, and `data` fields), not Mermaid.
> The `output_format` parameter is `"jsmind_json"` by default and is exposed for
> future extensibility.

### Registration (conceptual)

```yaml
skills:
  - name: smart_classroom_mindmap
    type: python
    module: openclaw_skills.mindmap.skill
    entrypoint: run
    working_dir: /path/to/edge-ai-suites/education-ai-suite/smart-classroom
```

### Input

```json
{
  "session_id": "20260509-143022-ab12",
  "summary_markdown": "## Teacher Summary\n- ...",
  "output_format": "jsmind_json",
  "include_raw": false
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `session_id` | string | one of `session_id`/`summary_markdown` | Existing session ID |
| `summary_markdown` | string | one of `session_id`/`summary_markdown` | Markdown summary text |
| `language` | string | no | `"en"` (default) or `"zh"` |
| `output_format` | string | no | `"jsmind_json"` (default) or `"raw"` |
| `include_raw` | bool | no | Also return raw LLM string (default `false`) |

### Output

```json
{
  "session_id": "20260509-143022-ab12",
  "mindmap": {
    "meta": {"name": "Quantum Mechanics", "author": "ai_assistant", "version": "1.0"},
    "format": "node_tree",
    "data": {
      "id": "root",
      "topic": "Quantum Mechanics",
      "children": [...]
    }
  },
  "format": "jsmind_json",
  "source": "summary_markdown"
}
```

| Field | Type | Description |
|---|---|---|
| `session_id` | string | Session used / created for this request |
| `mindmap` | object \| null | Parsed jsMind JSON; `null` when parsing failed |
| `raw_mindmap` | string | Raw LLM output (present when `include_raw=true` or parsing failed) |
| `format` | string | `"jsmind_json"` or `"raw"` (actual format of `mindmap`) |
| `source` | string | `"session_id"` or `"summary_markdown"` |

---

## Chaining Audio Summary → MindMap

The two skills are designed to chain: the output of `smart_classroom_audio_summary`
feeds directly into `smart_classroom_mindmap`.

### Step 1 – Audio Summary

```json
{
  "skill": "smart_classroom_audio_summary",
  "input": {
    "audio_file": "/data/classes/physics_lecture.mp3",
    "include_transcript": true
  }
}
```

Response:

```json
{
  "session_id": "20260509-143022-ab12",
  "summary_markdown": "## Teacher Summary\n- Wave-particle duality ...",
  "transcript": "TEACHER: Today we cover ...",
  "source": "audio_file"
}
```

### Step 2 – MindMap (using output from Step 1)

Pass `summary_markdown` and `session_id` from Step 1 directly:

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

Response:

```json
{
  "session_id": "20260509-143022-ab12",
  "mindmap": {
    "meta": {"name": "physics_lecture", "author": "ai_assistant", "version": "1.0"},
    "format": "node_tree",
    "data": {
      "id": "root",
      "topic": "Quantum Mechanics and Wave Theory",
      "children": [
        {
          "id": "wave_particle_duality",
          "topic": "Wave-Particle Duality",
          "children": [...]
        }
      ]
    }
  },
  "format": "jsmind_json",
  "source": "summary_markdown"
}
```

### OpenClaw orchestration example

```python
audio_result = openclaw.invoke("smart_classroom_audio_summary", {
    "audio_file": "/data/classes/physics_lecture.mp3",
    "include_transcript": True,
})

mindmap_result = openclaw.invoke("smart_classroom_mindmap", {
    "session_id":       audio_result["session_id"],
    "summary_markdown": audio_result["summary_markdown"],
    "output_format":    "jsmind_json",
})
```

---

## Behavior Notes

- **Minimum token check:** `Pipeline.run_mindmap()` enforces `mindmap.min_token` from
  `config.yaml` (default: 20 tokens). If the summary is too short, the pipeline
  returns an `insufficient_input` jsMind JSON structure. The skill parses and returns
  this normally with `format: "jsmind_json"`.

- **summary_markdown overwrites session summary:** When both `session_id` and
  `summary_markdown` are provided, the supplied markdown is written to that session's
  `summary.md` before running the pipeline. This lets you regenerate a mind map from
  updated content without creating a new session.

- **JSON parsing:** The skill strips markdown code fences before attempting to parse
  the LLM output as JSON. If parsing fails, `mindmap` is `null`, `raw_mindmap` is
  always included, and `format` is set to `"raw"`.

---

## Tests

Lightweight pytest unit tests (no ASR/LLM model loading) are in
`openclaw_skills/tests/`:

```bash
cd education-ai-suite/smart-classroom
python -m pytest openclaw_skills/tests/ -v
```
