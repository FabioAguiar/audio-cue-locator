<p align="center">
  <img src="https://raw.githubusercontent.com/FabioAguiar/project-assets/main/projects/audio-cue-locator/logos/svg/audio-cue-locator-logo.svg" alt="Audio Cue Locator logo" width="680" />
</p>

# Audio Cue Locator

Audio Cue Locator (ACL) is an independent tool for locating reference acoustic cues inside larger audio or video files. It accepts source media together with one or more reference cues, finds their temporal occurrences, and returns method-specific similarity scores in a structured, versioned result.

The same backend capability is available through two first-class interfaces: a standalone WebUI for interactive use and a versioned REST API v1 for programmatic integration.

> **Project status:** active development. Milestones M1-M6 are complete, delivering the canonical media pipeline, acoustic matching, the Analysis/Cue/Occurrence core, local SQLite/filesystem persistence, REST API v1, and the standalone WebUI. **M7 — Baseline Operacional Reproduzível** remains the active operational milestone and is packaging and validating that functional baseline as a reproducible local runtime. The authoritative status is recorded in [`docs/project-status/milestone-state.json`](docs/project-status/milestone-state.json).

## What Audio Cue Locator Does

ACL supports this workflow today:

```text
source audio/video
        +
reference cue(s)
        ↓
media preparation and canonicalization
        ↓
acoustic matching
        ↓
temporal occurrences with similarity scores
        ↓
structured, versioned result
```

- The **standalone WebUI** uploads source media and one or more cues, creates and follows an Analysis, presents cue-level outcomes and occurrences, supports audio audition, and downloads the Result JSON.
- The **REST API v1** exposes the same upload, Analysis lifecycle, Result, and audition capabilities to programmatic clients.

Both interfaces use the same Application boundary. The WebUI is an API client and does not contain a separate media-processing or matching implementation.

## Interface

### Home

<p align="center">
  <img src="https://raw.githubusercontent.com/FabioAguiar/project-assets/main/projects/audio-cue-locator/screenshots/exports/acl-home.png" alt="Audio Cue Locator home" width="920" />
</p>

The Home screen provides the complete starting workflow: select source media, add one or more reference cues, and start an Analysis.

### Analysis setup and results

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="https://raw.githubusercontent.com/FabioAguiar/project-assets/main/projects/audio-cue-locator/screenshots/exports/acl-analysis-setup.png" alt="Audio Cue Locator analysis setup" width="100%" />
    </td>
    <td width="50%" valign="top">
      <img src="https://raw.githubusercontent.com/FabioAguiar/project-assets/main/projects/audio-cue-locator/screenshots/exports/acl-analysis-results.png" alt="Audio Cue Locator analysis results" width="100%" />
    </td>
  </tr>
  <tr>
    <td valign="top"><strong>Analysis setup.</strong> Each cue can have an optional Name and optional Start/End source search bounds.</td>
    <td valign="top"><strong>Analysis results.</strong> Multiple cues are grouped independently with their match count, failure state, search bounds, and effective matching method.</td>
  </tr>
</table>

Start and End constrain where the complete cue is searched on the source-media timeline; they do not trim the cue. With both blank, ACL searches the full source. End alone searches `[0, End)`, Start alone searches `[Start, source end)`, and both search `[Start, End)`. The WebUI accepts `MM:SS`, `MM:SS.fraction`, `HH:MM:SS`, or `HH:MM:SS.fraction`, while the backend remains authoritative for duration-aware validation.

### Cue match details

<p align="center">
  <img src="https://raw.githubusercontent.com/FabioAguiar/project-assets/main/projects/audio-cue-locator/screenshots/exports/acl-cue-match-details.png" alt="Audio Cue Locator cue match details" width="920" />
</p>

For each matched cue, the detail view distinguishes:

- **Index** — the one-based index in canonical chronological order;
- **Rank** — the one-based similarity ordering, sorted by raw score descending, then earlier position, then canonical index;
- **Position** — the source-timeline position, displayed as `MM:SS` below one hour or `HH:MM:SS` from one hour, with fractional seconds floored for display only;
- **Similarity** — the raw method-specific score displayed to two decimals, not a percentage or statistical confidence.

Cue and occurrence play/stop controls fetch bounded WAV audition audio from the Analysis-scoped API endpoints. Only one playback is active at a time; playback does not perform matching in the browser.

### Similarity settings

<p align="center">
  <img src="https://raw.githubusercontent.com/FabioAguiar/project-assets/main/projects/audio-cue-locator/screenshots/exports/acl-similarity-settings.png" alt="Audio Cue Locator similarity settings" width="620" />
</p>

The WebUI's **Minimum similarity score** is an optional similarity threshold for newly created Analyses. Until a user sets an override, the WebUI omits `minimum_similarity_score` and the backend supplies its exact default. A custom value is captured in the next Analysis request for all of its cues. Changing or resetting the setting does not modify an Analysis that has already been created: its effective configuration remains preserved. The value is a similarity cutoff, not a percentage or calibrated statistical confidence.

See [`webui/README.md`](webui/README.md) for the complete interaction semantics.

## Acoustic Matching

The current implemented baseline is the versioned method **`normalized_cross_correlation_multi_v1`**. ACL canonicalizes the source and cues, computes normalized cross-correlation scores over valid source windows, and applies the Analysis's effective acceptance threshold. The current multi-occurrence producer:

- accepts candidates whose score meets or exceeds the effective threshold;
- suppresses overlapping full-cue windows after considering higher-scoring candidates first;
- returns the selected occurrences in chronological order, within the method's documented bound;
- supports independent outcomes for multiple cues in one Analysis.

Each Analysis preserves an effective configuration snapshot containing the canonicalization parameters, matching method and acceptance threshold, and configuration provenance. Omitting the REST override keeps the evidence-based backend default; an explicit `minimum_similarity_score` is captured exactly for that Analysis. This makes result-affecting configuration traceable and prevents later default changes from reinterpreting an existing Analysis.

A matching **score** expresses similarity according to this method. It is not a calibrated probability, is not statistical confidence, and must not be assumed comparable with scores from a different future method.

The detailed contracts and evidence are documented in [`docs/matching-contract.md`](docs/matching-contract.md), [`docs/matching-baseline.md`](docs/matching-baseline.md), [`docs/matching-acceptance.md`](docs/matching-acceptance.md), [`docs/matching-benchmark.md`](docs/matching-benchmark.md), [`docs/matching-regression.md`](docs/matching-regression.md), and [`docs/matching-robustness.md`](docs/matching-robustness.md).

## REST API v1

REST API v1 is a public application interface, not merely an internal transport for the WebUI. It exposes asynchronous Analysis creation and persisted lifecycle status under the `/api/v1` namespace.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Check API v1 liveness. |
| `POST` | `/api/v1/assets/source-media` | Upload a bounded source-media Asset. |
| `POST` | `/api/v1/assets/cue` | Upload a bounded cue Asset. |
| `POST` | `/api/v1/analyses` | Create an asynchronous Analysis from Asset identities. |
| `GET` | `/api/v1/analyses/{analysis_id}` | Read the current persisted Analysis status. |
| `GET` | `/api/v1/analyses/{analysis_id}/result` | Retrieve the structured Result for a succeeded Analysis. |
| `GET` | `/api/v1/analyses/{analysis_id}/cues/{cue_id}/audio` | Audition the original cue WAV for a completed Analysis. |
| `GET` | `/api/v1/analyses/{analysis_id}/cues/{cue_id}/occurrences/{occurrence_index}/audio` | Audition the source-time window represented by an accepted occurrence. |

A programmatic client typically:

1. uploads source media;
2. uploads one or more cues;
3. creates an Analysis;
4. polls its persisted status until a terminal state;
5. retrieves the structured Result after success;
6. optionally auditions cue or occurrence audio while the required stored media remains available.

Analysis states are `queued`, `running`, `succeeded`, and `failed`. Result retrieval and audition are lifecycle-gated, and `occurrence_index` in the audition route is zero-based against the canonical occurrence array. See [`docs/rest-api-v1-contract.md`](docs/rest-api-v1-contract.md) for request and response schemas, error envelopes, lifecycle behavior, upload rules, and audition guardrails.

## Architecture

ACL is implemented as a modular monolith with lightweight layered and Ports-and-Adapters boundaries:

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

The current stack comprises:

- **Python** for the backend and media-analysis application;
- **FastAPI** for REST API v1;
- **Pydantic** for transport validation and API schemas;
- **FFmpeg** for media probing, decoding, extraction, canonicalization, and occurrence audition rendering;
- **NumPy / SciPy** for numerical processing and acoustic matching;
- **SQLite** for local Analysis state and metadata;
- **local filesystem storage** for uploaded media and runtime artifacts;
- **React, TypeScript, and Vite** for the standalone WebUI.

The Core remains independent of HTTP, UI, database, filesystem, FFmpeg, and numerical implementation details. Infrastructure implements those mechanisms behind application-facing ports. The WebUI communicates exclusively through REST API v1.

See [`docs/architecture.md`](docs/architecture.md) for boundaries, runtime decisions, security constraints, risks, and trade-offs.

## Core Concepts

- **Asset** — an identifiable source media file, reference cue, or managed artifact;
- **Analysis** — a request to locate one or more cues in a source, with a persisted lifecycle and effective configuration;
- **Cue** — a reference acoustic sample used for matching;
- **Occurrence** — a temporal source location associated with a cue, a matching method, and a similarity score;
- **Analysis Result** — the structured, independently versioned output of a completed Analysis.

The model is intentionally domain-neutral. ACL locates acoustic references; it does not assign application-specific meaning to the detections.

## Local Operation

The backend and WebUI are built as two images from the repository's [`Dockerfile`](Dockerfile) and orchestrated by [`compose.yaml`](compose.yaml). Docker Engine with BuildKit and the Docker Compose plugin are required; Python, FFmpeg, Node, and project dependencies are installed inside the images.

From the repository root:

```bash
docker compose build
docker compose up
```

The current Compose configuration binds both services to host loopback:

- REST API v1: `http://localhost:18000/api/v1`
- WebUI: `http://localhost:18081`
- Health: `GET http://localhost:18000/api/v1/health`

The API container listens internally on port `8000`; the WebUI container reverse-proxies same-origin `/api/v1` requests to it. Stop the runtime without deleting its named data volume with:

```bash
docker compose down
```

See [`docs/local-operation.md`](docs/local-operation.md) for the runtime topology, build and health procedures, storage, retention, configuration, and known limitations. When a documented host port differs, [`compose.yaml`](compose.yaml) is the executable source of truth for the current binding.

## Validation and Development Status

The operational milestone cursor is intentionally separate from the milestone plan. M1-M6 are completed and locked; M7 remains active.

| Milestone | Focus | Operational status |
|---|---|---|
| M1 | Foundation and canonical media | Completed |
| M2 | Acoustic matching baseline | Completed |
| M3 | Analysis and structured results | Completed |
| M4 | Persistent local lifecycle | Completed |
| M5 | REST API v1 | Completed |
| M6 | Standalone WebUI | Completed |
| M7 | Reproducible operational baseline | Active |

The repository includes matching benchmarks, regression and robustness evidence, API integration coverage, WebUI end-to-end specifications, and an M7 release-validation procedure. The M7 record explicitly distinguishes authored validation procedures from executed evidence; its operational status must not be inferred as complete from the existence of those assets. See [`docs/baseline-validation.md`](docs/baseline-validation.md) and [`docs/milestones.md`](docs/milestones.md).

The supported local runtime is development-grade. Public or multi-user deployment remains out of scope until a separate security review addresses authentication, TLS, external exposure, and isolation.

## Future Matching Directions

`normalized_cross_correlation_multi_v1` remains the **current implemented baseline**. The techniques below are candidate directions for future investigation only. Except for the current baseline row, they are not implemented ACL capabilities, approved milestones, release commitments, or guarantees; each would be subject to benchmarking and empirical validation.

| Technique | Potential use | Example tunable parameters |
|---|---|---|
| **Normalized cross-correlation — CURRENT BASELINE** | Exact or near-exact cue matching; current ACL baseline | similarity threshold; potential future controls for minimum distance and maximum occurrences |
| Matched filtering | Detecting a known pattern in noise | threshold, normalization, window |
| GCC / GCC-PHAT | Robust temporal alignment | weighting, window, lag range |
| Spectrogram correlation | Similar spectral structure under moderate changes | FFT size, hop length, window, min/max frequency, threshold |
| MFCC similarity | Similar acoustic content despite some timbre/level variation | number of MFCCs, frame size, hop length, distance metric, threshold |
| Chroma matching | Musical, melodic, or harmonic matching | chroma bins, hop length, normalization, threshold |
| Dynamic Time Warping (DTW) | Similar sequences with moderate timing/speed variation | distance metric, band/window constraint, feature type, threshold |
| Audio fingerprinting | Recognition of recordings or modified copies | peak density, fan-out, time tolerance, frequency tolerance |
| Audio embeddings + similarity | Broader acoustic or semantic similarity | embedding model, window size, overlap, cosine threshold, top-k |

The parameter examples for exploratory methods are illustrative, not existing API fields. In the current baseline, only the similarity threshold is exposed as a request override; overlap suppression and the maximum-occurrence bound are method-defined rather than client-tunable.

Potential standalone or composed approaches may include spectrogram correlation, MFCC + DTW, Chroma + DTW, audio fingerprinting, and audio embeddings + similarity. These techniques may have method-specific hyperparameters and configuration schemas. Their inclusion would require explicit parameters, traceable effective configuration, reproducible results, benchmarks, regression testing, empirical validation, and a stable versioned matching-method identifier. Not all candidates are expected to be implemented, and none changes ACL into a general-purpose semantic sound-classification system.

## Design Principles

- validate acoustic assumptions before building sophistication around them;
- keep the analysis core independent of HTTP, UI, database, filesystem, and algorithm adapters;
- keep WebUI and programmatic clients on the same application boundary;
- separate control-plane metadata from large media artifacts;
- treat potentially long analyses as jobs without requiring distributed infrastructure;
- expose structured failures instead of converting processing errors into empty detections;
- make result-affecting parameters explicit and traceable;
- prefer reproducibility and measurable evidence over premature optimization;
- keep the local baseline simple enough to run, test, and understand without cloud infrastructure.

## Non-Goals and Current Limitations

The current project does not aim to provide:

- semantic sound classification without an appropriate reference or model;
- machine-learning model training as a core requirement;
- OCR or visual analysis;
- automated video editing;
- real-time audio streaming;
- distributed processing, Kubernetes, service-mesh, or broker infrastructure;
- mandatory cloud or object storage;
- enterprise authentication or multi-tenancy;
- calibrated statistical confidence without a supporting methodology.

Future matching research remains constrained to ACL's cue-location problem and does not implicitly expand these goals. The current matcher has method- and evidence-corpus-specific robustness limits, and more complex techniques must demonstrate a measured advantage before adoption. The packaged runtime is local-first, uses SQLite and filesystem storage, and does not by itself provide production deployment security controls.

## Documentation

The repository documentation remains the source of truth for detailed behavior:

- [`docs/vision.md`](docs/vision.md) — purpose, scope, constraints, success criteria, and open questions;
- [`docs/architecture.md`](docs/architecture.md) — architectural boundaries, responsibilities, runtime choices, risks, and validation criteria;
- [`docs/milestones.md`](docs/milestones.md) and [`docs/project-status/milestone-state.json`](docs/project-status/milestone-state.json) — planned capability progression and the separate operational milestone cursor;
- [`docs/rest-api-v1-contract.md`](docs/rest-api-v1-contract.md) — public REST API v1 transport contract;
- [`docs/local-operation.md`](docs/local-operation.md) — packaged local runtime, storage, retention, and operational procedures;
- [`docs/baseline-validation.md`](docs/baseline-validation.md) — M7 validation scope and evidence record;
- [`docs/matching-contract.md`](docs/matching-contract.md) and [`docs/matching-acceptance.md`](docs/matching-acceptance.md) — score, matching, and acceptance semantics;
- [`docs/analysis-effective-configuration.md`](docs/analysis-effective-configuration.md) and [`docs/analysis-result-schema.md`](docs/analysis-result-schema.md) — configuration traceability and structured Result schema;
- [`webui/README.md`](webui/README.md) — WebUI behavior, field semantics, result presentation, and API boundary.

## License

This project is licensed under the [MIT License](LICENSE).

Copyright © 2026 Fábio Aguiar.
