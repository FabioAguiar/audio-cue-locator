/**
 * Typed client for the four REST API v1 routes the WebUI is authorized to
 * call (docs/rest-api-v1-contract.md): the two bounded upload routes,
 * asynchronous Analysis creation, and Analysis status retrieval. This is
 * the only module that constructs a `fetch` request or knows the exact
 * request/response JSON shapes; `NewAnalysisPage.tsx` and
 * `useAnalysisPolling.ts` both depend on it instead of calling `fetch`
 * directly (M6-02, `intents/M6/M6-02/implementation-handoff.json`).
 *
 * This module never imports `src/audio_cue_locator/*`, never reads
 * SQLite or the backend filesystem, and never invents a request/response
 * shape beyond what `docs/rest-api-v1-contract.md` documents.
 */

/** Reuses the same `/api/v1` base the M6-01 scaffold already documents
 * (`webui/src/main.tsx`'s own `API_BASE_URL` constant). Declared
 * independently here rather than imported, since `webui/src/main.tsx` is
 * out of this issue's allowed edit paths and must not be modified. */
const API_BASE_URL = "/api/v1";

// --- Public v1 schemas (docs/rest-api-v1-contract.md) -----------------

export type AssetLogicalType =
  | "source_media"
  | "cue"
  | "derived_artifact"
  | "analysis_result";

export interface AssetPublic {
  identifier: string;
  logical_type: AssetLogicalType;
  sanitized_name: string;
  media_type: string;
  size_bytes: number;
  checksum: string;
  checksum_algorithm: "sha256";
}

export type AnalysisStatus = "queued" | "running" | "succeeded" | "failed";

/** S0003 adds `label` (optional, presentation-only) and
 * `trim_start_seconds`/`trim_end_seconds` (optional per-Cue source-media
 * search bounds, half-open `[start, end)`, in seconds; names retained for
 * compatibility). S0004 (`specs/S0004-
 * responsive-single-screen-home-design-convergence/spec.md`) binds these
 * fields in `NewAnalysisPage.tsx`'s per-cue Name/Start time/End time
 * inputs; every field here remains optional since a cue may still omit
 * them. */
export interface AnalysisCueReference {
  cue_id: string;
  asset_id: string;
  label?: string | null;
  trim_start_seconds?: number | null;
  trim_end_seconds?: number | null;
}

export interface AnalysisLifecycleTimestamps {
  queued_at: string;
  running_at: string | null;
  succeeded_at: string | null;
  failed_at: string | null;
}

export type AnalysisFailureCategory =
  | "invalid_input"
  | "unsupported_media"
  | "decode_or_canonicalization_failure"
  | "matching_failure"
  | "resource_limit"
  | "internal_failure";

export interface AnalysisStructuredError {
  category: AnalysisFailureCategory;
  message: string;
}

export interface AnalysisPublic {
  analysis_id: string;
  status: AnalysisStatus;
  source_asset_id: string;
  cues: AnalysisCueReference[];
  lifecycle_timestamps: AnalysisLifecycleTimestamps;
  result_reference: string | null;
  structured_error: AnalysisStructuredError | null;
}

/** The shared v1 Error envelope (docs/rest-api-v1-contract.md, "Shared
 * Error contract and sanitization policy"). Every field here is safe to
 * present to a user as-is; never reformulate or append to `message`. */
export type ErrorCode =
  | "validation_error"
  | "unsupported_media"
  | "resource_limit_exceeded"
  | "resource_not_found"
  | "lifecycle_conflict"
  | "result_not_ready"
  | "analysis_failed"
  | "internal_error";

export interface ErrorPublic {
  error_code: ErrorCode;
  message: string;
  correlation_id: string;
}

// --- Result type and network-vs-contract-error distinction -------------

/**
 * Every client function resolves to one of these two outcomes for a
 * request that reached the server and produced a response: `ok: true`
 * with the parsed success body, or `ok: false` with the parsed shared
 * Error envelope. A request that never reached the server, or whose
 * response could not be parsed as JSON at all, rejects with
 * `ApiNetworkError` instead of resolving — callers (in particular
 * `useAnalysisPolling.ts`) rely on this distinction to treat only
 * `ApiNetworkError` as transient/retryable, per this issue's own
 * transient-network-error decision
 * (`intents/M6/M6-02/implementation-handoff.json#/decisions/1`).
 */
export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: ErrorPublic };

/** Raised only for a transport-level failure: the request never produced
 * a parseable response (a rejected `fetch`, a timeout, or a body that is
 * not valid JSON). Never raised for a well-formed non-2xx response — that
 * case resolves as `{ ok: false, error }` instead. */
export class ApiNetworkError extends Error {
  readonly cause: unknown;

  constructor(message: string, cause: unknown) {
    super(message);
    this.name = "ApiNetworkError";
    this.cause = cause;
  }
}

async function parseJsonOrThrowNetworkError(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch (cause) {
    throw new ApiNetworkError(
      "Response body could not be parsed as JSON",
      cause,
    );
  }
}

async function requestJson<T>(
  input: RequestInfo,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(input, init);
  } catch (cause) {
    throw new ApiNetworkError("Network request failed", cause);
  }

  const body = await parseJsonOrThrowNetworkError(response);

  if (response.ok) {
    return { ok: true, data: body as T };
  }
  return { ok: false, error: body as ErrorPublic };
}

async function uploadAsset(
  path: "/assets/source-media" | "/assets/cue",
  file: File,
): Promise<ApiResult<AssetPublic>> {
  const formData = new FormData();
  formData.append("file", file, file.name);
  return requestJson<AssetPublic>(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
  });
}

/** `POST /api/v1/assets/source-media` — bounded source-media Asset upload. */
export function uploadSourceMedia(file: File): Promise<ApiResult<AssetPublic>> {
  return uploadAsset("/assets/source-media", file);
}

/** `POST /api/v1/assets/cue` — bounded cue Asset upload. */
export function uploadCue(file: File): Promise<ApiResult<AssetPublic>> {
  return uploadAsset("/assets/cue", file);
}

/** `POST /api/v1/analyses` — asynchronous Analysis creation from Asset
 * identities. Resolves once the server responds `202 Accepted`; it does
 * not wait for acoustic matching to complete. */
export function createAnalysis(
  sourceAssetId: string,
  cues: AnalysisCueReference[],
): Promise<ApiResult<AnalysisPublic>> {
  return requestJson<AnalysisPublic>(`${API_BASE_URL}/analyses`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_asset_id: sourceAssetId, cues }),
  });
}

/** `GET /api/v1/analyses/{analysis_id}` — current persisted Analysis
 * status. The sole source of every lifecycle state
 * `useAnalysisPolling.ts` reflects; never inferred locally. */
export function getAnalysisStatus(
  analysisId: string,
): Promise<ApiResult<AnalysisPublic>> {
  return requestJson<AnalysisPublic>(
    `${API_BASE_URL}/analyses/${encodeURIComponent(analysisId)}`,
    { method: "GET" },
  );
}
