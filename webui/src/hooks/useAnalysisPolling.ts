import { useEffect, useState } from "react";

import {
  ApiNetworkError,
  AnalysisPublic,
  ErrorPublic,
  getAnalysisStatus,
} from "../api/client";

/**
 * Polls `GET /api/v1/analyses/{analysis_id}` and exposes the current
 * persisted Analysis lifecycle to `NewAnalysisPage.tsx`. Every displayed
 * state transition comes from a real API response; this hook never
 * infers or simulates a state locally
 * (`docs/rest-api-v1-contract.md#Analysis status and Result retrieval`).
 *
 * Interval, backoff, and transient-network-error handling follow this
 * issue's own explicit decisions, recorded in
 * `intents/M6/M6-02/implementation-handoff.json#/decisions/0` and
 * `#/decisions/1` (M6-02 formal issue section 17 names both as this
 * handoff's own responsibility rather than implementer judgment):
 *
 * - Base polling interval: 2000ms while status is `queued` or `running`.
 * - Backoff: 1.5x the previous interval after each poll that still
 *   returns the same non-terminal status, capped at 10000ms.
 * - Polling stops immediately once a response reports `succeeded` or
 *   `failed`.
 * - A network-level failure (`ApiNetworkError` — a rejected fetch,
 *   timeout, or a response that could not be parsed as JSON) is treated
 *   as transient: retry on the same interval/backoff schedule, up to 5
 *   consecutive transient failures. A well-formed error response (the
 *   shared `ErrorPublic` envelope) is never treated as transient and is
 *   surfaced immediately, without retry.
 * - After 5 consecutive transient failures, polling stops and
 *   `connectionLost` becomes `true`. No `error_code`, `message`, or
 *   `correlation_id` is fabricated for a failure the backend never
 *   returned; `connectionLost` is a distinct, client-local signal.
 */

const BASE_INTERVAL_MS = 2000;
const BACKOFF_MULTIPLIER = 1.5;
const MAX_INTERVAL_MS = 10000;
const MAX_CONSECUTIVE_TRANSIENT_FAILURES = 5;

function isTerminalStatus(status: AnalysisPublic["status"]): boolean {
  return status === "succeeded" || status === "failed";
}

export interface UseAnalysisPollingResult {
  /** The most recently observed persisted Analysis, or `null` before the
   * first successful poll. */
  analysis: AnalysisPublic | null;
  /** The most recent well-formed API error response, if any. Present only
   * when the backend itself returned a non-2xx response (for example, the
   * Analysis identifier was not found); never fabricated locally. */
  error: ErrorPublic | null;
  /** `true` once 5 consecutive transient network failures have occurred
   * and polling has stopped without ever reaching a terminal status. */
  connectionLost: boolean;
  /** `true` while polling is actively scheduled (before a terminal status,
   * a well-formed error, or `connectionLost` is reached). */
  isPolling: boolean;
}

export function useAnalysisPolling(
  analysisId: string | null,
): UseAnalysisPollingResult {
  const [analysis, setAnalysis] = useState<AnalysisPublic | null>(null);
  const [error, setError] = useState<ErrorPublic | null>(null);
  const [connectionLost, setConnectionLost] = useState<boolean>(false);
  const [isPolling, setIsPolling] = useState<boolean>(false);

  useEffect(() => {
    setAnalysis(null);
    setError(null);
    setConnectionLost(false);

    if (!analysisId) {
      setIsPolling(false);
      return;
    }

    let cancelled = false;
    let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
    let currentIntervalMs = BASE_INTERVAL_MS;
    let consecutiveTransientFailures = 0;

    setIsPolling(true);

    const pollOnce = async (): Promise<void> => {
      let result;
      try {
        result = await getAnalysisStatus(analysisId);
      } catch (cause) {
        if (cancelled) {
          return;
        }
        if (cause instanceof ApiNetworkError) {
          consecutiveTransientFailures += 1;
          if (consecutiveTransientFailures >= MAX_CONSECUTIVE_TRANSIENT_FAILURES) {
            setConnectionLost(true);
            setIsPolling(false);
            return;
          }
          scheduleNext();
          return;
        }
        throw cause;
      }

      if (cancelled) {
        return;
      }

      consecutiveTransientFailures = 0;

      if (!result.ok) {
        setError(result.error);
        setIsPolling(false);
        return;
      }

      setAnalysis(result.data);

      if (isTerminalStatus(result.data.status)) {
        setIsPolling(false);
        return;
      }

      scheduleNext();
    };

    const scheduleNext = (): void => {
      if (cancelled) {
        return;
      }
      timeoutHandle = setTimeout(() => {
        void pollOnce();
      }, currentIntervalMs);
      currentIntervalMs = Math.min(
        currentIntervalMs * BACKOFF_MULTIPLIER,
        MAX_INTERVAL_MS,
      );
    };

    void pollOnce();

    return () => {
      cancelled = true;
      if (timeoutHandle !== undefined) {
        clearTimeout(timeoutHandle);
      }
    };
  }, [analysisId]);

  return { analysis, error, connectionLost, isPolling };
}
