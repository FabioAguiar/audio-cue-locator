# Audio Cue Locator

Audio Cue Locator is an independent tool for locating reference acoustic cues inside larger audio or video files.

The project is designed to accept a source media file together with one or more reference cues, analyze the audio, and return structured temporal occurrences with similarity scores that can be inspected by a user or consumed programmatically.

> **Project status:** active development. Milestones M1-M6 are complete, delivering the canonical media pipeline, acoustic matching, the Analysis/Cue/Occurrence core, local SQLite/filesystem persistence, a versioned REST API v1, and a standalone WebUI. The project is currently at **M7 — Baseline Operacional Reproduzível**, packaging that functional baseline into a reproducible, clean-environment-buildable local runtime; see [Local Operation](#local-operation) below.

## What the Project Intends to Do

Conceptually, Audio Cue Locator will support workflows such as:

```text
source audio/video
        +
reference cue(s)
        ↓
media preparation
        ↓
acoustic matching
        ↓
temporal occurrences
        ↓
structured result
```

A future analysis may produce information such as:

```json
{
  "cue_id": "start",
  "occurrences": [
    {
      "start_ms": 12438,
      "score": 0.9821,
      "method": "normalized_cross_correlation"
    }
  ]
}
```

The exact result contract, supported formats, timestamp precision, thresholds, and matching behavior are still subject to validation during the planned milestones.

## Product Direction

The project is intended to provide two ways to use the same underlying capability:

- a **standalone WebUI** for submitting media and reference cues, following analysis progress, inspecting detections, and downloading structured results;
- a **versioned REST API** for programmatic integration.

Both interfaces are intended to use the same application boundary. The WebUI will not maintain a separate audio-analysis implementation.

## Architectural Direction

The planned baseline is a modular monolith with lightweight layered and Ports-and-Adapters boundaries:

```text
WebUI
  ↓ HTTP
REST API
  ↓
Application
  ↓
Core
  ↓ ports
Infrastructure
  ├── media probing / decoding / canonicalization
  ├── acoustic matching
  ├── asset storage
  ├── analysis persistence
  └── local job execution
```

The current architectural baseline selects:

- **Python** for backend and processing;
- **FastAPI** for the REST interface;
- **Pydantic** for transport validation and API schemas;
- **FFmpeg** for media probing, decoding, extraction, and canonicalization;
- **NumPy / SciPy** as the initial numerical foundation for acoustic matching;
- **SQLite** for local analysis state and metadata;
- **local filesystem storage** for media and runtime artifacts.

These choices establish the initial architecture but remain subject to evidence-driven revision where the project documentation explicitly identifies open technical questions.

## Core Concepts

The planned product model centers on a small set of generic concepts:

- **Asset** — an identifiable source media file, reference cue, or managed artifact;
- **Analysis** — a request to locate one or more cues in a source;
- **Cue** — a reference acoustic sample used for matching;
- **Occurrence** — a temporal location associated with a cue and a matching score;
- **Analysis Result** — the structured, versioned output of an analysis.

The core is intentionally domain-neutral. Audio Cue Locator is responsible for locating acoustic references, not for assigning application-specific meaning to what those detections represent.

## Score vs. Confidence

The project deliberately distinguishes a matching **score** from statistical **confidence**.

A score expresses the output of a matching method. It must not be presented as calibrated confidence unless a future methodology provides evidence that supports that interpretation.

## Current Milestone

The current operational milestone is:

**M7 — Baseline Operacional Reproduzível**

Milestones M1 through M6 are complete: canonical media pipeline, acoustic matching, the Analysis/Cue/Occurrence core with structured results, local SQLite/filesystem persistence, versioned REST API v1, and a standalone WebUI. M7 packages that existing functional baseline into a reproducible, clean-environment-buildable local runtime with controlled dependencies, predictable FFmpeg availability, safe example configuration, and a documented build/start/stop/health procedure. See [`docs/milestones.md`](docs/milestones.md) for the full milestone plan and Definition of Done.

See [Local Operation](#local-operation) below for the packaged runtime this milestone establishes.

## Planned Milestones

| Milestone | Focus | Expected outcome |
|---|---|---|
| M1 | Foundation and canonical media | Deterministic canonical-audio pipeline for validated inputs |
| M2 | Acoustic matching | Validated single-cue matching baseline with timestamp and score |
| M3 | Analysis and structured results | Core Analysis/Cue/Occurrence model with multi-cue and multi-occurrence results |
| M4 | Persistent local lifecycle | Local assets, SQLite persistence, and asynchronous local analysis lifecycle |
| M5 | REST API v1 | Versioned API for uploads, analysis creation, status, and results |
| M6 | Standalone WebUI | Complete human-facing workflow built exclusively on the REST API |
| M7 | Reproducible operational baseline | Packaged, observable, constrained, end-to-end validated local product |

The milestone plan is intentionally incremental. Distributed execution, remote object storage, advanced authentication, and similar infrastructure are not part of the initial baseline unless later evidence establishes a concrete need.

## Design Principles

Audio Cue Locator is being developed around the following principles:

- validate acoustic assumptions before building sophisticated interfaces around them;
- keep the analysis core independent of HTTP, UI, database, and filesystem details;
- keep WebUI and programmatic clients on the same application boundary;
- separate control-plane metadata from large media artifacts;
- treat potentially long analyses as jobs without requiring distributed infrastructure initially;
- expose structured failures instead of silently converting processing errors into empty detections;
- make parameters that affect results explicit and traceable;
- prefer reproducibility and measurable evidence over premature optimization;
- keep the local baseline simple enough to run, test, and understand without cloud infrastructure.

## Non-Goals for the Initial Baseline

The initial project does not aim to provide:

- semantic sound classification without a reference cue;
- machine-learning model training as a core requirement;
- OCR or visual analysis;
- automated video editing;
- real-time audio streaming;
- distributed processing;
- Kubernetes or service-mesh infrastructure;
- mandatory cloud or object storage;
- enterprise authentication or multi-tenancy;
- calibrated statistical confidence without supporting methodology.

These areas may only be reconsidered later if the project develops a concrete requirement for them.

## Repository Documentation

The current repository documentation is the authoritative starting point for the project:

- [`docs/vision.md`](docs/vision.md) — product purpose, scope, constraints, success criteria, and open questions;
- [`docs/architecture.md`](docs/architecture.md) — architectural boundaries, responsibilities, runtime choices, risks, and validation criteria;
- [`docs/milestones.md`](docs/milestones.md) — planned capability progression and completion criteria;
- [`docs/project-status/milestone-state.json`](docs/project-status/milestone-state.json) — operational milestone cursor.

The milestone plan and operational state are intentionally separate: `docs/milestones.md` describes planned evolution, while the state file records which milestone is currently active.

## Local Operation

The complete backend (FastAPI REST API v1, SQLite/filesystem persistence, FFmpeg-backed Media Processing) and the standalone WebUI are packaged as two container images built from a single [`Dockerfile`](Dockerfile) and orchestrated by [`compose.yaml`](compose.yaml):

```bash
docker compose build
docker compose up
```

- REST API v1: `http://localhost:8000/api/v1` (health check: `GET /api/v1/health`)
- WebUI: `http://localhost:8080`

Stop the runtime with:

```bash
docker compose down
```

See [`docs/local-operation.md`](docs/local-operation.md) for the full clean-build, startup, shutdown, storage, health-check, and configuration reference, including the two-container topology's rationale and known limitations.

## Development Status

Local installation and execution are documented in [Local Operation](#local-operation) above, via the packaged Docker/Compose runtime established during M7. Public or multi-user deployment is out of scope until a separate, explicitly authorized security review permits it (see [`docs/architecture.md`](docs/architecture.md)).

## License

This project is licensed under the [MIT License](LICENSE).

Copyright © 2026 Fábio Aguiar.
