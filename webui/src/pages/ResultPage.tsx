import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  AnalysisCueReference,
  AnalysisPublic,
  ApiNetworkError,
  ErrorPublic,
  fetchCueAuditionAudio,
  fetchOccurrenceAuditionAudio,
} from "../api/client";
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
 * S0014 presents one collapsible group per Cue and annotates chronological
 * occurrence rows with a deterministic similarity rank. Audition bodies are
 * fetched lazily through the typed client, with one shared playback resource
 * owner for the entire card. The exact raw Result text remains the download
 * source and is never mutated or re-serialized.
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

type PlaybackStatus = "loading" | "playing";

type PlaybackRequest =
  | { key: string; cueId: string; kind: "cue" }
  | {
      key: string;
      cueId: string;
      kind: "occurrence";
      occurrenceIndex: number;
    };

type PlaybackTarget = PlaybackRequest & { status: PlaybackStatus };

interface PlaybackResources {
  controller: AbortController | null;
  audio: HTMLAudioElement | null;
  objectUrl: string | null;
}

interface PlaybackError {
  cueId: string;
  message: string;
}

function cueIdentity(
  cueId: string,
  cueFilenamesById: Record<string, string>,
  label?: string | null,
): string {
  const filename = cueFilenamesById[cueId];
  const normalizedLabel = label?.trim();
  if (normalizedLabel) {
    return normalizedLabel;
  }
  if (filename) {
    return filename;
  }
  return cueId;
}

function formatClockTime(seconds: number): string {
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainingSeconds = wholeSeconds % 60;
  const minuteText = String(
    hours > 0 ? minutes : Math.floor(wholeSeconds / 60),
  ).padStart(2, "0");
  const secondText = String(remainingSeconds).padStart(2, "0");

  if (wholeSeconds < 3600) {
    return `${minuteText}:${secondText}`;
  }
  return `${String(hours).padStart(2, "0")}:${minuteText}:${secondText}`;
}

function occurrenceRanks(occurrences: Occurrence[]): number[] {
  const rankedIndices = occurrences
    .map((_occurrence, index) => index)
    .sort((leftIndex, rightIndex) => {
      const left = occurrences[leftIndex];
      const right = occurrences[rightIndex];
      return (
        right.score - left.score ||
        left.temporal_position - right.temporal_position ||
        leftIndex - rightIndex
      );
    });

  const ranks = new Array<number>(occurrences.length);
  rankedIndices.forEach((canonicalIndex, rankIndex) => {
    ranks[canonicalIndex] = rankIndex + 1;
  });
  return ranks;
}

function playbackButtonLabel(
  target: PlaybackTarget | null,
  key: string,
  idleLabel: string,
): string {
  if (target?.key !== key) {
    return `Play ${idleLabel}`;
  }
  return target.status === "loading"
    ? `Loading ${idleLabel}`
    : `Stop ${idleLabel}`;
}

function PlaybackButton({
  request,
  label,
  activeTarget,
  onActivate,
}: {
  request: PlaybackRequest;
  label: string;
  activeTarget: PlaybackTarget | null;
  onActivate: (request: PlaybackRequest) => void;
}): JSX.Element {
  const isTarget = activeTarget?.key === request.key;
  const isLoading = isTarget && activeTarget.status === "loading";
  const isPlaying = isTarget && activeTarget.status === "playing";

  return (
    <button
      type="button"
      className={`audio-preview-button${isTarget ? " is-active" : ""}`}
      aria-label={playbackButtonLabel(activeTarget, request.key, label)}
      aria-busy={isLoading || undefined}
      title={playbackButtonLabel(activeTarget, request.key, label)}
      onClick={() => onActivate(request)}
    >
      <span aria-hidden="true">{isLoading ? "…" : isPlaying ? "■" : "▶"}</span>
    </button>
  );
}

function OccurrenceItem({
  occurrence,
  canonicalIndex,
  rank,
  cueId,
  cueName,
  activeTarget,
  onPlayback,
}: {
  occurrence: Occurrence;
  canonicalIndex: number;
  rank: number;
  cueId: string;
  cueName: string;
  activeTarget: PlaybackTarget | null;
  onPlayback: (request: PlaybackRequest) => void;
}): JSX.Element {
  const visibleIndex = canonicalIndex + 1;
  const playbackKey = `occurrence:${cueId}:${canonicalIndex}`;

  return (
    <div
      className="occurrence-row"
      role="row"
      data-occurrence-index={canonicalIndex}
      data-similarity-rank={rank}
    >
      <span className="occurrence-cell occurrence-cell--index" role="cell">
        {visibleIndex}
      </span>
      <span className="occurrence-cell" role="cell">
        {rank}
      </span>
      <span
        className="occurrence-cell occurrence-position"
        role="cell"
        title={`Raw position: ${occurrence.temporal_position} seconds`}
      >
        {formatClockTime(occurrence.temporal_position)}
      </span>
      <span
        className="occurrence-cell occurrence-score-badge"
        role="cell"
        title={`Raw similarity score: ${occurrence.score}`}
      >
        {occurrence.score.toFixed(2)}
      </span>
      <span className="occurrence-cell occurrence-cell--preview" role="cell">
        <PlaybackButton
          request={{
            key: playbackKey,
            cueId,
            kind: "occurrence",
            occurrenceIndex: canonicalIndex,
          }}
          label={`match ${visibleIndex} for ${cueName}`}
          activeTarget={activeTarget}
          onActivate={onPlayback}
        />
      </span>
    </div>
  );
}

function CueResultGroup({
  cue,
  index,
  cueReference,
  cueName,
  matchingMethod,
  expanded,
  activeTarget,
  playbackError,
  onToggle,
  onPlayback,
}: {
  cue: CueResult;
  index: number;
  cueReference?: AnalysisCueReference;
  cueName: string;
  matchingMethod: string;
  expanded: boolean;
  activeTarget: PlaybackTarget | null;
  playbackError: PlaybackError | null;
  onToggle: (cueId: string) => void;
  onPlayback: (request: PlaybackRequest) => void;
}): JSX.Element {
  const detailsId = `cue-result-details-${index}`;
  const start = cueReference?.trim_start_seconds;
  const end = cueReference?.trim_end_seconds;
  const hasWindow =
    (start !== null && start !== undefined) ||
    (end !== null && end !== undefined);
  const count =
    cue.outcome.kind === "occurrences" ? cue.outcome.occurrences.length : 0;
  const statusLabel =
    cue.outcome.kind === "failure"
      ? "Failed"
      : `${count} ${count === 1 ? "match" : "matches"}`;
  const ranks =
    cue.outcome.kind === "occurrences"
      ? occurrenceRanks(cue.outcome.occurrences)
      : [];

  return (
    <article
      className={`cue-result-group cue-result-group--${cue.outcome.kind}`}
      data-cue-id={cue.cue_id}
    >
      <div className="cue-result-group__header">
        <PlaybackButton
          request={{ key: `cue:${cue.cue_id}`, cueId: cue.cue_id, kind: "cue" }}
          label={`cue ${cueName}`}
          activeTarget={activeTarget}
          onActivate={onPlayback}
        />
        <div className="cue-result-identity">
          <span
            className={`result-note${index % 2 === 1 ? " result-note--alt" : ""}`}
            aria-hidden="true"
          >
            ♫
          </span>
          <h3>{cueName}</h3>
        </div>
        {hasWindow && (
          <div className="cue-result-window" aria-label="Source search window">
            {start !== null && start !== undefined && (
              <span title={`Raw source-search start: ${start} seconds`}>
                Start {formatClockTime(start)}
              </span>
            )}
            {start !== null &&
              start !== undefined &&
              end !== null &&
              end !== undefined && <span aria-hidden="true">·</span>}
            {end !== null && end !== undefined && (
              <span title={`Raw source-search end: ${end} seconds`}>
                End {formatClockTime(end)}
              </span>
            )}
          </div>
        )}
        <span className="cue-result-status">{statusLabel}</span>
        <div className="cue-result-method">
          <span>Method</span>
          <strong title={`Matching method: ${matchingMethod}`}>
            {matchingMethod}
          </strong>
        </div>
        <button
          type="button"
          className="cue-disclosure-button"
          aria-expanded={expanded}
          aria-controls={detailsId}
          aria-label={`${expanded ? "Collapse" : "Expand"} results for ${cueName}`}
          title={`${expanded ? "Collapse" : "Expand"} results for ${cueName}`}
          onClick={() => onToggle(cue.cue_id)}
        >
          <span aria-hidden="true">{expanded ? "−" : "+"}</span>
        </button>
      </div>
      {playbackError?.cueId === cue.cue_id && (
        <p className="playback-error" role="alert">
          {playbackError.message}
        </p>
      )}
      <div
        id={detailsId}
        className="cue-result-group__details"
        hidden={!expanded}
      >
        {cue.outcome.kind === "occurrences" && (
          <div
            className="occurrence-table"
            role="table"
            aria-label={`Matches for ${cueName}`}
          >
            <div className="occurrence-header" role="row">
              <span role="columnheader">#</span>
              <span role="columnheader">Rank</span>
              <span role="columnheader">Position</span>
              <span role="columnheader">Similarity</span>
              <span role="columnheader">Preview</span>
            </div>
            {cue.outcome.occurrences.map((occurrence, canonicalIndex) => (
              <OccurrenceItem
                key={`${cue.cue_id}-${canonicalIndex}`}
                occurrence={occurrence}
                canonicalIndex={canonicalIndex}
                rank={ranks[canonicalIndex]}
                cueId={cue.cue_id}
                cueName={cueName}
                activeTarget={activeTarget}
                onPlayback={onPlayback}
              />
            ))}
          </div>
        )}
        {cue.outcome.kind === "no_match" && (
          <p className="no-match-text">No match found for this cue.</p>
        )}
        {cue.outcome.kind === "failure" && (
          <p className="failure-text" role="alert">
            {cue.outcome.failure.category}: {cue.outcome.failure.message}
          </p>
        )}
      </div>
    </article>
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
  const [collapsedCueIds, setCollapsedCueIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [activeTarget, setActiveTarget] = useState<PlaybackTarget | null>(null);
  const [playbackError, setPlaybackError] = useState<PlaybackError | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const activeTargetRef = useRef<PlaybackTarget | null>(null);
  const playbackVersionRef = useRef(0);
  const playbackResourcesRef = useRef<PlaybackResources>({
    controller: null,
    audio: null,
    objectUrl: null,
  });

  const updateActiveTarget = useCallback(
    (target: PlaybackTarget | null): void => {
      activeTargetRef.current = target;
      setActiveTarget(target);
    },
    [],
  );

  const disposePlayback = useCallback((clearError = true): void => {
    playbackVersionRef.current += 1;
    const resources = playbackResourcesRef.current;
    resources.controller?.abort();
    if (resources.audio) {
      resources.audio.onended = null;
      resources.audio.onerror = null;
      resources.audio.onplaying = null;
      resources.audio.pause();
      try {
        resources.audio.currentTime = 0;
      } catch {
        // Some browsers reject seeking before media metadata is available.
      }
    }
    if (resources.objectUrl) {
      URL.revokeObjectURL(resources.objectUrl);
    }
    playbackResourcesRef.current = {
      controller: null,
      audio: null,
      objectUrl: null,
    };
    updateActiveTarget(null);
    if (clearError) {
      setPlaybackError(null);
    }
  }, [updateActiveTarget]);

  const startPlayback = useCallback(
    async (request: PlaybackRequest): Promise<void> => {
      if (activeTargetRef.current?.key === request.key) {
        disposePlayback();
        return;
      }

      disposePlayback();
      const version = playbackVersionRef.current;
      const controller = new AbortController();
      playbackResourcesRef.current.controller = controller;
      updateActiveTarget({ ...request, status: "loading" });

      try {
        const outcome =
          request.kind === "cue"
            ? await fetchCueAuditionAudio(
                analysisId,
                request.cueId,
                controller.signal,
              )
            : await fetchOccurrenceAuditionAudio(
                analysisId,
                request.cueId,
                request.occurrenceIndex,
                controller.signal,
              );

        if (
          controller.signal.aborted ||
          playbackVersionRef.current !== version
        ) {
          return;
        }
        playbackResourcesRef.current.controller = null;

        if (!outcome.ok) {
          disposePlayback();
          setPlaybackError({
            cueId: request.cueId,
            message: outcome.error.message,
          });
          return;
        }

        const objectUrl = URL.createObjectURL(outcome.blob);
        if (playbackVersionRef.current !== version) {
          URL.revokeObjectURL(objectUrl);
          return;
        }

        const audio = new Audio(objectUrl);
        playbackResourcesRef.current.audio = audio;
        playbackResourcesRef.current.objectUrl = objectUrl;
        audio.onplaying = () => {
          if (playbackVersionRef.current === version) {
            updateActiveTarget({ ...request, status: "playing" });
          }
        };
        audio.onended = () => {
          if (playbackVersionRef.current === version) {
            disposePlayback(false);
          }
        };
        audio.onerror = () => {
          if (playbackVersionRef.current === version) {
            disposePlayback();
            setPlaybackError({
              cueId: request.cueId,
              message: "Unable to play audio preview.",
            });
          }
        };

        await audio.play();
        if (playbackVersionRef.current === version && !audio.paused) {
          updateActiveTarget({ ...request, status: "playing" });
        }
      } catch (cause) {
        if (
          controller.signal.aborted ||
          playbackVersionRef.current !== version
        ) {
          return;
        }
        disposePlayback();
        setPlaybackError({
          cueId: request.cueId,
          message:
            cause instanceof ApiNetworkError
              ? "Unable to load audio preview."
              : "Unable to play audio preview.",
        });
      }
    },
    [analysisId, disposePlayback, updateActiveTarget],
  );

  useEffect(() => {
    sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    headingRef.current?.focus();
    // Runs once, when this card is first mounted for a newly created
    // Analysis (S0004 section 4.10): move scroll/focus context toward
    // Results without stealing focus on later re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => disposePlayback, [analysisId, disposePlayback]);

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
          setCollapsedCueIds(new Set());
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

  const cueReferencesById: Record<string, AnalysisCueReference> = {};
  for (const cue of analysis?.cues ?? []) {
    cueReferencesById[cue.cue_id] = cue;
  }

  function toggleCueGroup(cueId: string): void {
    const willCollapse = !collapsedCueIds.has(cueId);
    if (willCollapse && activeTargetRef.current?.cueId === cueId) {
      disposePlayback();
    }
    setCollapsedCueIds((current) => {
      const next = new Set(current);
      if (next.has(cueId)) {
        next.delete(cueId);
      } else {
        next.add(cueId);
      }
      return next;
    });
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
          <div className="cue-result-groups">
            {envelope.result.cues.map((cue, index) => {
              const cueReference = cueReferencesById[cue.cue_id];
              const cueName = cueIdentity(
                cue.cue_id,
                cueFilenamesById,
                cueReference?.label,
              );
              return (
                <CueResultGroup
                  key={cue.cue_id}
                  cue={cue}
                  index={index}
                  cueReference={cueReference}
                  cueName={cueName}
                  matchingMethod={envelope.result.configuration.matching.method}
                  expanded={!collapsedCueIds.has(cue.cue_id)}
                  activeTarget={activeTarget}
                  playbackError={playbackError}
                  onToggle={toggleCueGroup}
                  onPlayback={(request) => void startPlayback(request)}
                />
              );
            })}
          </div>
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
