import React, { useEffect, useState } from "react";

import { ApiNetworkError, ErrorPublic } from "../api/client";
import {
  AnalysisResultEnvelope,
  CueResult,
  Occurrence,
  downloadAnalysisResultText,
  fetchAnalysisResult,
} from "../api/downloadResult";
import { useAnalysisPolling } from "../hooks/useAnalysisPolling";

/**
 * Results view for an Analysis identified by `analysisId`: lists every
 * cue's outcome once the Analysis reaches `succeeded`, shows the
 * already-polled `structured_error` once it reaches `failed`, and offers
 * a byte-identical download of the Result JSON (M6-03,
 * `intents/M6/M6-03/implementation-handoff.json`).
 *
 * Scope, exactly as authorized:
 * - Reuse `useAnalysisPolling` (M6-02) to obtain the terminal
 *   `AnalysisPublic`; never poll or infer lifecycle state independently.
 * - Call `GET /api/v1/analyses/{analysis_id}/result` only once status is
 *   `succeeded`; never while `queued`/`running`, and never at all when
 *   `failed` (decisions[3]) -- a `failed` Analysis's outcome comes only
 *   from its already-polled `structured_error`.
 * - Render three structurally distinct terminal outcomes: analysis-level
 *   `failed`, cue-level `no_match`, and cue-level `failure` (decisions[2]);
 *   none is visually or structurally conflated with another.
 * - Present every score using its raw value next to an explicit method
 *   label, never as "confidence" or a percentage (decisions[0]).
 * - Serve the download from the exact raw response text captured by
 *   `downloadResult.ts`, never a re-serialization of the parsed object
 *   (decisions[1]).
 *
 * Explicitly out of scope: any temporal/timeline visualization (M6-04),
 * a media player/waveform view, and recomputing or relabeling the score.
 * This component is not yet mounted by `webui/src/main.tsx`: wiring it
 * into the application shell is outside this issue's authorized edit
 * paths, mirroring M6-02's own `NewAnalysisPage.tsx`, which also remains
 * unmounted after that handoff.
 */

export interface ResultPageProps {
  analysisId: string;
}

type ResultFetchState =
  | { status: "not_requested" }
  | { status: "loading" }
  | { status: "loaded"; envelope: AnalysisResultEnvelope; rawText: string }
  | { status: "api_error"; error: ErrorPublic }
  | { status: "network_error" };

function ScoreLabel({ occurrence }: { occurrence: Occurrence }): JSX.Element {
  return (
    <span>
      Similarity score ({occurrence.matching_method}): {occurrence.score}
    </span>
  );
}

function OccurrenceItem({
  occurrence,
}: {
  occurrence: Occurrence;
}): JSX.Element {
  return (
    <li>
      <div>Position: {occurrence.temporal_position}s</div>
      <div>
        <ScoreLabel occurrence={occurrence} />
      </div>
      {occurrence.end !== null ? <div>End: {occurrence.end}s</div> : null}
    </li>
  );
}

function CueOutcomeItem({ cue }: { cue: CueResult }): JSX.Element {
  if (cue.outcome.kind === "occurrences") {
    return (
      <li>
        <h3>Cue: {cue.cue_id}</h3>
        <ul>
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
      <li>
        <h3>Cue: {cue.cue_id}</h3>
        <p>No match found for this cue.</p>
      </li>
    );
  }

  return (
    <li>
      <h3>Cue: {cue.cue_id}</h3>
      <p role="alert">
        Processing failed for this cue ({cue.outcome.failure.category}):{" "}
        {cue.outcome.failure.message}
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
    <div role="alert">
      <h2>{heading}</h2>
      <p>
        {error.message} (error_code: {error.error_code}, correlation_id:{" "}
        {error.correlation_id})
      </p>
    </div>
  );
}

export default function ResultPage({
  analysisId,
}: ResultPageProps): JSX.Element {
  const { analysis, error, connectionLost, isPolling } =
    useAnalysisPolling(analysisId);
  const [resultState, setResultState] = useState<ResultFetchState>({
    status: "not_requested",
  });

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

  if (connectionLost) {
    return <p role="alert">Unable to reach the server to check this Analysis's status.</p>;
  }

  if (error) {
    return <ApiErrorNotice heading="Unable to load Analysis status" error={error} />;
  }

  if (!analysis || isPolling) {
    return <p>Waiting for this Analysis to reach a terminal state...</p>;
  }

  if (analysis.status === "failed") {
    return (
      <div role="alert">
        <h2>Analysis failed</h2>
        {analysis.structured_error ? (
          <p>
            {analysis.structured_error.category}:{" "}
            {analysis.structured_error.message}
          </p>
        ) : null}
      </div>
    );
  }

  // analysis.status === "succeeded" from here on.
  if (resultState.status === "not_requested" || resultState.status === "loading") {
    return <p>Loading Analysis result...</p>;
  }

  if (resultState.status === "network_error") {
    return (
      <p role="alert">
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

  return (
    <section>
      <h2>Analysis result</h2>
      <p>Method: {envelope.result.method}</p>
      <ul>
        {envelope.result.cues.map((cue) => (
          <CueOutcomeItem key={cue.cue_id} cue={cue} />
        ))}
      </ul>
      <button
        type="button"
        onClick={() =>
          downloadAnalysisResultText(analysis.analysis_id, rawText)
        }
      >
        Download result JSON
      </button>
    </section>
  );
}
