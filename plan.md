# Project: Audio Transcription Pipeline
<!-- Hydrate with: "read plan.md and create tasks with dependencies" -->
<!-- Last updated: 2026-03-04 | Session: zoom-pipeline-v2 -->

## Status Legend
- `pending`   — not started
- `in_progress` — currently active
- `completed` — done and verified
- `blocked`   — waiting on dependency

---

## Tasks

### T1 · Set up S3 bucket and IAM roles
- **status:** `completed`
- **blocks:** T2, T3
- **description:** Create S3 bucket for raw Zoom recordings, output transcripts, and configure IAM roles for read/write access.
- **notes:** Bucket name: `zoom-transcripts-prod`. IAM role ARN saved to `.env.example`.

---

### T2 · Build Zoom webhook receiver
- **status:** `completed`
- **blocks:** T5
- **blockedBy:** T1
- **description:** FastAPI endpoint that receives Zoom recording-complete webhooks, validates signatures, and enqueues the download job.
- **notes:** Deployed to Lambda. Webhook secret stored in AWS Secrets Manager.

---

### T3 · Implement S3 download worker
- **status:** `completed`
- **blocks:** T5
- **blockedBy:** T1
- **description:** Worker that pulls raw `.m4a` files from Zoom cloud storage and stages them in S3 under `raw/`.
- **notes:** Handles Zoom OAuth token refresh automatically.

---

### T4 · Integrate Whisper transcription
- **status:** `in_progress`
- **blocks:** T6, T7
- **blockedBy:** none
- **description:** Wrap OpenAI Whisper API (or local model) to transcribe audio files. Output raw transcript JSON with word-level timestamps.
- **notes:** Currently testing `whisper-large-v3`. Cost ~$0.006/min. Need to decide on local vs. API.

---

### T5 · Wire webhook → download → transcription pipeline
- **status:** `pending`
- **blocks:** T8
- **blockedBy:** T2, T3, T4
- **description:** Connect the three components end-to-end. Webhook triggers download worker; on completion, enqueues transcription job.
- **notes:** Use SQS queues between stages for resilience.

---

### T6 · Build diarization layer
- **status:** `pending`
- **blocks:** T7
- **blockedBy:** T4
- **description:** Add speaker diarization using pyannote.audio. Merge diarization output with Whisper timestamps to produce speaker-labeled transcript.
- **notes:** Requires HuggingFace token. pyannote model: `pyannote/speaker-diarization-3.1`.

---

### T7 · LLM post-processing (cleanup + summary)
- **status:** `pending`
- **blocks:** T8
- **blockedBy:** T4, T6
- **description:** Pass labeled transcript through Claude to: (1) clean filler words, (2) generate structured meeting summary with action items.
- **notes:** Use claude-sonnet-4-6. Prompt template in `prompts/summary.md`.

---

### T8 · Output delivery (email + Notion)
- **status:** `pending`
- **blocks:** none
- **blockedBy:** T5, T7
- **description:** Deliver final transcript and summary via: (1) email to meeting host, (2) Notion page in shared workspace.
- **notes:** Notion API token in Secrets Manager. Email via SES.

---

## Dependency Graph

```
T1 (completed)
├── T2 (completed) ──────────────────────┐
└── T3 (completed) ──────────────────────┤
                                         ▼
T4 (in_progress) ──────────────────── T5 (pending) ──┐
├── T6 (pending)                                      │
│   └── T7 (pending) ──────────────────────────────── T8 (pending)
└──────────────────┘
```

## Session Notes

- **2026-03-01:** T1, T2, T3 completed. T4 started — testing Whisper models.
- **2026-03-04:** T4 still in progress. Blocked on local vs. API cost decision. T6, T7, T8 not yet started.

## Hydration Instructions for Claude

When asked to hydrate this plan:
1. Create a Task for each `Tx` entry
2. Set `status` from the status field
3. Wire `addBlockedBy` from each task's `blockedBy` list
4. Skip creating tasks with status `completed` — just note them as context
5. Begin work on tasks whose blockedBy tasks are all `completed` or absent
