import React, { useEffect, useRef, useState } from "react";

import { AnalysisPublic, ApiNetworkError, ErrorPublic } from "../api/client";
import ActivityIndicator from "../components/ActivityIndicator";
import {
  AnalysisResultEnvelope,
  CueResult,
  Occurrence,
  downloadAnalysisResultText,
  fetchAnalysisResult,
} from "../api/downloadResult";

/**
 * Results as the conditional third Home card (S0004,
 * `specs/S0004-responsive-single-screen-home-design-convergence/spec.md`).
 *
 * This component no longer polls independently: `NewAnalysisPage` is the
 * single `useAnalysisPolling` owner and passes the current `AnalysisPublic`
 * (or its absence/error) down as props. This component still owns fetching
 * `GET /api/v1/analyses/{analysis_id}/result` exactly once the passed-in
 * `analysis.status` reaches `succeeded`, and still serves the download from
 * the exact raw response text captured by `downloadResult.ts` -- never a
 * re-serialization of the parsed object.
 *
 * Every score is presented as a raw similarity value next to its matching
 * method, never as "confidence" or a percentage. `no_match`, cue-level
 * `failure`, and Analysis-level `failed` are rendered as three distinct,
 * never-conflated outcomes.
 */

export interface ResultPageProps {
  analysisId: string;
  analysis: AnalysisPublic | null;
  pollingError: ErrorPublic | null;
  connectionLost: boolean;
  isPolling: boolean;
  /** Current-session uploaded cue filenames by `cue_id`, used as the
   * presentation-identity fallback when a cue has no S0003 `label`. Never
   * persisted as backend metadata. */
  cueFilenamesById: Record<string, string>;
}

type ResultFetchState =
  | { status: "not_requested" }
  | { status: "loading" }
  | { status: "loaded"; envelope: AnalysisResultEnvelope; rawText: string }
  | { status: "api_error"; error: ErrorPublic }
  | { status: "network_error" };

function cueIdentity(
  cueId: string,
  cueFilenamesById: Record<string, string>,
  label?: string | null,
): { primary: string; secondary: string | null } {
  const filename = cueFilenamesById[cueId];
  if (label) {
    return { primary: label, secondary: filename ?? null };
  }
  if (filename) {
    return { primary: filename, secondary: null };
  }
  return { primary: cueId, secondary: null };
}

function OccurrenceItem({
  occurrence,
}: {
  occurrence: Occurrence;
}): JSX.Element {
  return (
    <li className="occurrence-item">
      <span>Position: {occurrence.temporal_position}s</span>
      {occurrence.end !== null && <span>End: {occurrence.end}s</span>}
      <span
        className="occurrence-score-badge"
        title={`Raw similarity score: ${occurrence.score}`}
      >
        Similarity score: {occurrence.score}
      </span>
      <span>Method: {occurrence.matching_method}</span>
    </li>
  );
}

function CueOutcomeItem({
  cue,
  index,
  cueFilenamesById,
  cueLabelsById,
}: {
  cue: CueResult;
  index: number;
  cueFilenamesById: Record<string, string>;
  cueLabelsById: Record<string, string | null | undefined>;
}): JSX.Element {
  const identity = cueIdentity(
    cue.cue_id,
    cueFilenamesById,
    cueLabelsById[cue.cue_id],
  );

  const heading = (
    <div className="result-cue-heading">
      <span
        className={`result-note${index % 2 === 1 ? " result-note--alt" : ""}`}
        aria-hidden="true"
      >
        ♫
      </span>
      <div>
        <h3>{identity.primary}</h3>
        {identity.secondary && <small>{identity.secondary}</small>}
      </div>
    </div>
  );

  if (cue.outcome.kind === "occurrences") {
    return (
      <li className="result-row">
        {heading}
        <ul className="occurrence-list">
          {cue.outcome.occurrences.map((occurrence, index) => (
            <OccurrenceItem
              key={`${cue.cue_id}-${index}`}
              occurrence={occurrence}
            />
          ))}
        </ul>
      </li>
    );
  }

  if (cue.outcome.kind === "no_match") {
    return (
      <li className="result-row result-row--no-match">
        {heading}
        <p className="no-match-text">No match found for this cue.</p>
      </li>
    );
  }

  return (
    <li className="result-row result-row--failure">
      {heading}
      <p className="failure-text" role="alert">
        {cue.outcome.failure.category}: {cue.outcome.failure.message}
      </p>
    </li>
  );
}

function ApiErrorNotice({
  heading,
  error,
}: {
  heading: string;
  error: ErrorPublic;
}): JSX.Element {
  return (
    <div className="analysis-failed-notice" role="alert">
      <h3>{heading}</h3>
      <p>
        {error.message} (error_code: {error.error_code}, correlation_id:{" "}
        {error.correlation_id})
      </p>
    </div>
  );
}

function totalOccurrenceCount(envelope: AnalysisResultEnvelope): number {
  return envelope.result.cues.reduce((total, cue) => {
    if (cue.outcome.kind === "occurrences") {
      return total + cue.outcome.occurrences.length;
    }
    return total;
  }, 0);
}

function matchCountLabel(count: number): string {
  if (count === 1) {
    return "1 match found";
  }
  return `${count} matches found`;
}

export default function ResultPage({
  analysisId,
  analysis,
  pollingError,
  connectionLost,
  isPolling,
  cueFilenamesById,
}: ResultPageProps): JSX.Element {
  const [resultState, setResultState] = useState<ResultFetchState>({
    status: "not_requested",
  });
  const headingRef = useRef<HTMLHeadingElement>(null);
  const sectionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    headingRef.current?.focus();
    // Runs once, when this card is first mounted for a newly created
    // Analysis (S0004 section 4.10): move scroll/focus context toward
    // Results without stealing focus on later re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!analysis || analysis.status !== "succeeded") {
      setResultState({ status: "not_requested" });
      return;
    }

    let cancelled = false;
    setResultState({ status: "loading" });

    fetchAnalysisResult(analysis.analysis_id)
      .then((outcome) => {
        if (cancelled) {
          return;
        }
        if (outcome.ok) {
          setResultState({
            status: "loaded",
            envelope: outcome.envelope,
            rawText: outcome.rawText,
          });
        } else {
          setResultState({ status: "api_error", error: outcome.error });
        }
      })
      .catch((cause) => {
        if (cancelled) {
          return;
        }
        if (cause instanceof ApiNetworkError) {
          setResultState({ status: "network_error" });
          return;
        }
        throw cause;
      });

    return () => {
      cancelled = true;
    };
  }, [analysis?.status, analysis?.analysis_id]);

  const cueLabelsById: Record<string, string | null | undefined> = {};
  for (const cue of analysis?.cues ?? []) {
    cueLabelsById[cue.cue_id] = cue.label;
  }

  function renderBody(): JSX.Element {
    if (connectionLost) {
      return (
        <p role="alert" className="form-alert">
          Unable to reach the server to check this Analysis's status. The
          Analysis may still be in progress on the server.
        </p>
      );
    }

    if (pollingError) {
      return (
        <ApiErrorNotice
          heading="Unable to load Analysis status"
          error={pollingError}
        />
      );
    }

    if (analysis?.status === "failed") {
      return (
        <div className="analysis-failed-notice" role="alert">
          <h3>Analysis failed</h3>
          {analysis.structured_error ? (
            <p>
              {analysis.structured_error.category}:{" "}
              {analysis.structured_error.message}
            </p>
          ) : null}
        </div>
      );
    }

    if (analysis?.status === "succeeded") {
      if (
        resultState.status === "not_requested" ||
        resultState.status === "loading"
      ) {
        return <p className="results-loading">Loading Analysis result…</p>;
      }

      if (resultState.status === "network_error") {
        return (
          <p role="alert" className="form-alert">
            Unable to reach the server to load this Analysis result.
          </p>
        );
      }

      if (resultState.status === "api_error") {
        return (
          <ApiErrorNotice
            heading="Unable to load Analysis result"
            error={resultState.error}
          />
        );
      }

      const { envelope, rawText } = resultState;
      const matchCount = totalOccurrenceCount(envelope);

      return (
        <>
          <div className="match-badge">
            <span aria-hidden="true">✓</span>
            <strong>{matchCountLabel(matchCount)}</strong>
          </div>
          <ul className="results-list">
            {envelope.result.cues.map((cue, index) => (
              <CueOutcomeItem
                key={cue.cue_id}
                cue={cue}
                index={index}
                cueFilenamesById={cueFilenamesById}
                cueLabelsById={cueLabelsById}
              />
            ))}
          </ul>
          <button
            type="button"
            className="download-button"
            onClick={() =>
              downloadAnalysisResultText(analysis.analysis_id, rawText)
            }
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 3v11m0 0 4-4m-4 4-4-4M5 15v4h14v-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Download result JSON
          </button>
        </>
      );
    }

    if (
      !analysis ||
      isPolling ||
      analysis.status === "queued" ||
      analysis.status === "running"
    ) {
      return <ActivityIndicator phase="locating" />;
    }

    return <ActivityIndicator phase="locating" />;
  }

  return (
    <section
      ref={sectionRef}
      className="panel results-panel"
      aria-labelledby="results-title"
    >
      <div className="panel-heading results-heading">
        <div className="heading-group">
          <span className="heading-icon heading-icon--pink" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <rect x="4" y="12" width="3.5" height="8" rx="1.5" fill="currentColor" />
              <rect x="10.2" y="7" width="3.5" height="13" rx="1.5" fill="currentColor" />
              <rect x="16.5" y="3" width="3.5" height="17" rx="1.5" fill="currentColor" />
            </svg>
          </span>
          <div>
            <h2 id="results-title" ref={headingRef} tabIndex={-1}>
              Results
            </h2>
            <p>Found matches for your audio cues.</p>
          </div>
        </div>
      </div>

      {renderBody()}
    </section>
  );
}
