# M4 Persistence and Lifecycle Review

Status: implemented, awaiting the separate ASF test phase. Scope: M4-07 and
the persistent lifecycle delivered by M4-01 through M4-06.

This review records the reproducible checks added for the M4 Definition of
Done. It does not claim that the checks passed: the implementation route
explicitly prohibits test execution, so execution and reduced pass/fail
evidence belong to the next ASF phase.

## SQLite and filesystem responsibility inspection

The responsibility boundary is intentionally asymmetric:

- `SQLiteAnalysisRepository` persists Analysis identity, lifecycle state,
  Asset identifiers, Cue references, effective configuration, timestamps,
  owned-Asset identifiers, an opaque result reference, and an optional
  structured error. Its `analyses` table declares only `TEXT` columns; it has
  no media-content, payload, sidecar, or `BLOB` column.
- `LocalFilesystemAssetStorage` writes the accepted payload directly to one
  extensionless file named by the internally generated canonical UUID. It
  writes no JSON or metadata sidecar. Informative names and media types never
  select the path.

The automated inspection is split along those existing responsibilities:

- `test_sqlite_schema_and_values_are_metadata_only` uses a temporary SQLite
  file, inspects `PRAGMA table_info(analyses)`, checks the SQLite storage class
  of every persisted value, and rejects both `BLOB` declarations and byte
  values.
- `test_ingest_returns_opaque_identity_checksum_and_exact_byte_round_trip`
  uses a temporary storage root and checks that its only child is the opaque
  identity-keyed file containing exactly the accepted bytes.
- `test_identical_inputs_receive_distinct_exclusive_storage_locations` checks
  that repeated names or bytes produce independent identity-keyed files and no
  metadata sidecars.

These checks intentionally inspect the documented schema and physical local
adapter layout only where AC-07 requires it. Other tests continue to assert
public port behavior rather than private SQL strings or helper call order.

## Acceptance-criterion traceability

| Criterion | Automated evidence added or reused |
|---|---|
| AC-01 — repository persistence and lifecycle validation | `tests/test_analysis_repository.py`: reopen round trip, duplicate/not-found behavior, state queries, terminal result/error persistence, invalid-transition atomicity; `tests/test_analysis_lifecycle.py`: exhaustive lifecycle transition vocabulary (reused unchanged). |
| AC-02 — Asset identity, checksum, and path safety | `tests/test_asset_storage.py`: bytes-like round trips, independent SHA-256, UUID identity, exclusive placement/collision behavior, invalid ingest/read/delete paths, symlink rejection, and missing identifiers. |
| AC-03 — asynchronous bounded local execution | `tests/test_local_executor_and_recovery.py::test_submit_runs_outside_caller_and_enforces_configured_concurrency`: worker identity, returned Futures, deterministic claim barriers, and an observed maximum equal to the configured bound. |
| AC-04 — persisted controlled failure | `tests/test_local_executor_and_recovery.py::test_controlled_matching_failure_is_persisted_as_structured_error`: deterministic invalid canonical-array shape through the public executor contract and a reopened SQLite record in `FAILED`. |
| AC-05 — restart/recovery | `tests/test_local_executor_and_recovery.py::test_startup_recovery_resolves_reopened_running_analysis_without_retry`: persisted `RUNNING`, repository reopen, fixed interrupted error, queued preservation, and empty second pass. |
| AC-06 — cleanup isolation | `tests/test_artifact_retention.py::test_real_adapters_isolate_cleanup_between_simultaneously_persisted_owners`: real SQLite/filesystem adapters, two simultaneous persisted owners, one eligible owner, one protected owner, unique bytes, and a shared Cue reference. |
| AC-07 — SQLite metadata / filesystem bytes separation | The three automated inspection checks described above plus this review. |

## Isolation and determinism

Every new database and storage root is created beneath pytest's per-test
temporary directory. No repository fixture, developer storage path, runtime
volume, network service, broker, object store, FFmpeg process, or retained
media/database is involved.

Executor concurrency uses `threading.Event` synchronization and observable
worker identities/counters. Five-second waits are deadlock guards only; no
assertion depends on sleeping for a scheduler-dependent duration. Executor
operations that need durable state open the real SQLite adapter within the
worker thread, preserving SQLite's connection-thread ownership while testing
the production adapter rather than an in-memory persistence fake.

The controlled failure uses a deterministic two-dimensional `float32` source,
which violates the executor's public canonical-array precondition. This keeps
the failure local and reproducible while proving Analysis-scoped structured
failure persistence; it does not add FFmpeg or external-media dependencies.

## Cleanup ownership interpretation

"Concurrently owned" is implemented as the validated minimum invariant: two
Analysis records exist in SQLite at the same time, one old and terminal and
one protected in `RUNNING`. They reference distinct source Assets and a shared
Cue Asset. Cleanup may delete the eligible Analysis's unique bytes, but the
protected Analysis's unique source and the shared Cue must remain readable.
Simultaneous executor activity is not asserted because the operational state
did not establish it as an additional requirement.

## Limitations and next validation step

- Tests were not executed during implementation. The next ASF test route must
  run the repository-native relevant suite and retain only reduced, sanitized,
  criterion-traceable results.
- The schema check protects the current SQLite table and persisted-value
  storage classes; it is not a general prohibition on all future SQLite schema
  evolution. Any future payload table must extend the inspection deliberately.
- The filesystem layout check covers the local adapter. It does not prescribe
  a layout for a future remote or object-storage adapter.
- `InMemoryResultReferenceStore` remains the documented non-durable default;
  durable result-body storage is outside M4-07.

No deployment, Docker, configuration, environment, secret, workflow, broker,
remote-storage, or production-code change is required by this implementation.
