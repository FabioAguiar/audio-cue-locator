/**
 * Fetches the Analysis Result envelope and exposes both a parsed, typed
 * view for rendering and the exact raw response text for download, so the
 * two are guaranteed to originate from the same response body and the
 * downloaded file is never re-serialized from the parsed object (M6-03,
 * `intents/M6/M6-03/implementation-handoff.json#/decisions/1`).
 *
 * This module never imports `src/audio_cue_locator/*`, never reads SQLite
 * or the backend filesystem, and calls
 * `GET /api/v1/analyses/{analysis_id}/result` exactly once per
 * `fetchAnalysisResult` invocation.
 */

import { ApiNetworkError, ErrorPublic } from "./client";

const API_BASE_URL = "/api/v1";

// --- Analysis Result envelope schemas ----------------------------------
// docs/analysis-result-schema.md, docs/occurrence-temporal-semantics-and-
// policy.md, and docs/rest-api-v1-contract.md's AnalysisResultEnvelope.
// This module declares its own copies rather than importing them from
// webui/src/api/client.ts, which does not yet declare them and is not an
// authorized edit path for this issue.

export type CueFailureCategory =
  | "invalid_input"
  | "unsupported_media"
  | "decode_or_canonicalization_failure"
  | "matching_failure"
  | "resource_limit"
  | "internal_failure";

/** docs/occurrence-temporal-semantics-and-policy.md#1: `temporal_position`
 * is mandatory; `end` is optional and method-dependent, and is documented
 * as always absent for `normalized_cross_correlation_v1`. `score` is
 * method-specific similarity, never confidence. */
export interface Occurrence {
  cue_id: string;
  temporal_position: number;
  score: number;
  matching_method: string;
  end: number | null;
}

export interface CueFailure {
  category: CueFailureCategory;
  message: string;
}

/** docs/analysis-result-schema.md#4: the closed `CueOutcome` union. Every
 * key is structurally present; the unused pair of `occurrences`/`failure`
 * is `null`, never omitted. */
export type CueOutcome =
  | { kind: "occurrences"; occurrences: Occurrence[]; failure: null }
  | { kind: "no_match"; occurrences: null; failure: null }
  | { kind: "failure"; occurrences: null; failure: CueFailure };

export interface CueResult {
  cue_id: string;
  outcome: CueOutcome;
}

export interface CanonicalizationSnapshot {
  sample_rate_hz: number;
  channels: number;
  sample_format: string;
  normalization: {
    enabled: boolean;
    method: string;
    target_peak_amplitude: number;
  };
}

export interface MatchingSnapshot {
  method: string;
  acceptance_threshold: number;
}

export interface EffectiveConfigurationSnapshot {
  canonicalization: CanonicalizationSnapshot;
  matching: MatchingSnapshot;
  configuration_source_name: string;
}

/** The Analysis-level structured error (same {category, message} shape as
 * a per-cue `CueFailure`, but a distinct field, never conflated with one:
 * it describes a failure of the whole Analysis, not of one cue. */
export type AnalysisLevelStructuredError = CueFailure;

/** docs/analysis-result-schema.md#1: present if, and only if,
 * `final_state == "failed"`. In practice, `GET .../result` only ever
 * returns 200 for a `succeeded` Analysis (docs/rest-api-v1-contract.md's
 * lifecycle gate), so `final_state` is expected to always be
 * `"completed"` on a successful response; this type still models the
 * documented `"failed"` possibility rather than narrowing it away. */
export interface AnalysisResult {
  schema_version: string;
  analysis_id: string;
  final_state: "completed" | "failed";
  method: string;
  configuration: EffectiveConfigurationSnapshot;
  cues: CueResult[];
  structured_error: AnalysisLevelStructuredError | null;
}

/** docs/rest-api-v1-contract.md's `AnalysisResultEnvelope`: `api_version`
 * is always `"v1"`; `result_schema_version` is copied from the stored
 * Result body's own `schema_version`; `result` is the unmodified Result
 * body. */
export interface AnalysisResultEnvelope {
  api_version: "v1";
  result_schema_version: string;
  result: AnalysisResult;
}

// --- Fetch outcome -------------------------------------------------------

/**
 * Resolves once `GET /api/v1/analyses/{analysis_id}/result` produces a
 * parseable response: `ok: true` with both the parsed envelope and the
 * exact raw response text on 200, or `ok: false` with the parsed shared
 * `ErrorPublic` envelope on a non-2xx response (for example
 * `result_not_ready`, `analysis_failed`, or `resource_not_found`). Rejects
 * with `ApiNetworkError` only for a transport-level failure (a rejected
 * fetch, timeout, or a response body that is not valid JSON at all) —
 * never for a well-formed non-2xx response.
 */
export type FetchAnalysisResultOutcome =
  | { ok: true; envelope: AnalysisResultEnvelope; rawText: string }
  | { ok: false; error: ErrorPublic };

/**
 * Fetches `GET /api/v1/analyses/{analysis_id}/result` exactly once. The
 * response body's raw text is captured before any parsing; the same
 * captured text is then parsed once to produce the typed envelope this
 * function returns. Callers must reuse this same `rawText` for any
 * download action rather than re-serializing the parsed envelope, so the
 * rendered view and the downloaded file are always sourced from one
 * response body (decisions[1]).
 *
 * Callers must only invoke this once the Analysis's persisted status is
 * `"succeeded"` (decisions[3]); calling it while `"queued"`, `"running"`,
 * or `"failed"` is guaranteed to reach the endpoint's own documented
 * lifecycle gate rather than a Result body.
 */
export async function fetchAnalysisResult(
  analysisId: string,
): Promise<FetchAnalysisResultOutcome> {
  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}/analyses/${encodeURIComponent(analysisId)}/result`,
      { method: "GET" },
    );
  } catch (cause) {
    throw new ApiNetworkError("Network request failed", cause);
  }

  let rawText: string;
  try {
    rawText = await response.text();
  } catch (cause) {
    throw new ApiNetworkError(
      "Response body could not be read",
      cause,
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch (cause) {
    throw new ApiNetworkError(
      "Response body could not be parsed as JSON",
      cause,
    );
  }

  if (response.ok) {
    return {
      ok: true,
      envelope: parsed as AnalysisResultEnvelope,
      rawText,
    };
  }
  return { ok: false, error: parsed as ErrorPublic };
}

// --- Byte-identical download ---------------------------------------------

/**
 * Triggers a browser download of `rawText` unmodified -- never
 * `JSON.stringify`d from a parsed object -- so the downloaded file is
 * byte-identical to the `GET /api/v1/analyses/{analysis_id}/result`
 * response body it was captured from (decisions[1]).
 */
export function downloadAnalysisResultText(
  analysisId: string,
  rawText: string,
): void {
  const blob = new Blob([rawText], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `analysis-${analysisId}-result.json`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    URL.revokeObjectURL(url);
  }
}
