import React, { useRef, useState } from "react";

import {
  ApiNetworkError,
  AnalysisCueReference,
  ErrorPublic,
  createAnalysis,
  uploadCue,
  uploadSourceMedia,
} from "../api/client";
import ActivityIndicator, {
  type ActivityPhase,
} from "../components/ActivityIndicator";
import { useAnalysisPolling } from "../hooks/useAnalysisPolling";
import ResultPage from "./ResultPage";

/**
 * Source media, Cues, lifecycle, and conditional Results as one Home
 * workflow (S0004,
 * `specs/S0004-responsive-single-screen-home-design-convergence/spec.md`).
 *
 * This component is the sole owner of `analysis_id` and the sole caller of
 * `useAnalysisPolling`; `ResultPage` receives the polled `AnalysisPublic`
 * as props instead of polling independently, so the same mounted Analysis
 * is never polled by two components at once.
 *
 * Source-media type/size/duration and source-window bounds
 * checking, and acoustic matching all remain backend-authoritative. This
 * component only performs presentation-layer time-text parsing and local
 * format/cross-field validation before ever calling `createAnalysis`.
 */

const MAX_CUES = 20;

const SOURCE_ACCEPT =
  "audio/wav,.wav,video/mp4,.mp4,.m4v,video/quicktime,.mov,video/webm,.webm,video/x-matroska,.mkv,video/x-msvideo,.avi";

interface CueFileEntry {
  /** Stable client-side key for React list rendering; distinct from the
   * `cue_id` sent to the API. */
  key: string;
  file: File | null;
  name: string;
  startText: string;
  endText: string;
}

type SubmissionPhase = "idle" | ActivityPhase;

let nextCueKey = 1;

function newCueFileEntry(): CueFileEntry {
  const entry: CueFileEntry = {
    key: `cue-${nextCueKey}`,
    file: null,
    name: "",
    startText: "",
    endText: "",
  };
  nextCueKey += 1;
  return entry;
}

// --- Deterministic UI time parsing (S0004 section 4.6) -------------------
//
// Accepted non-empty forms: MM:SS, MM:SS.fraction, HH:MM:SS,
// HH:MM:SS.fraction. All components are non-negative decimal digits;
// seconds is >= 0 and < 60; for HH:MM:SS the minute component is >= 0 and
// < 60; fractional seconds are allowed only on the final component; no
// sign, exponent notation, or free-form words are accepted. This is
// presentation parsing only -- backend validation remains authoritative
// for source-duration-relative bounds.

type TimeParseResult =
  | { kind: "empty" }
  | { kind: "invalid" }
  | { kind: "value"; seconds: number };

function parseCueTimeText(raw: string): TimeParseResult {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return { kind: "empty" };
  }

  const parts = trimmed.split(":");
  if (parts.length !== 2 && parts.length !== 3) {
    return { kind: "invalid" };
  }

  for (let index = 0; index < parts.length; index += 1) {
    const isLast = index === parts.length - 1;
    const pattern = isLast ? /^\d+(\.\d+)?$/ : /^\d+$/;
    if (!pattern.test(parts[index])) {
      return { kind: "invalid" };
    }
  }

  const numbers = parts.map((part) => Number(part));
  if (!numbers.every(Number.isFinite)) {
    return { kind: "invalid" };
  }
  const hours = numbers.length === 3 ? numbers[0] : 0;
  const minutes = numbers.length === 3 ? numbers[1] : numbers[0];
  const seconds = numbers[numbers.length - 1];

  if (numbers.length === 3 && minutes >= 60) {
    return { kind: "invalid" };
  }
  if (seconds >= 60) {
    return { kind: "invalid" };
  }

  const total = hours * 3600 + minutes * 60 + seconds;
  if (!Number.isFinite(total)) {
    return { kind: "invalid" };
  }
  return { kind: "value", seconds: total };
}

function ApiErrorNotice({ error }: { error: ErrorPublic }): JSX.Element {
  return (
    <p role="alert" className="form-alert">
      {error.message} (error_code: {error.error_code}, correlation_id:{" "}
      {error.correlation_id})
    </p>
  );
}

interface NewAnalysisPageProps {
  minimumSimilarityScore: number | null;
}

export default function NewAnalysisPage({
  minimumSimilarityScore,
}: NewAnalysisPageProps): JSX.Element {
  const minimumSimilarityScoreRef = useRef(minimumSimilarityScore);
  minimumSimilarityScoreRef.current = minimumSimilarityScore;
  const [sourceMediaFile, setSourceMediaFile] = useState<File | null>(null);
  const [isDraggingSource, setIsDraggingSource] = useState(false);
  const [cueEntries, setCueEntries] = useState<CueFileEntry[]>([
    newCueFileEntry(),
  ]);
  const [submitting, setSubmitting] = useState(false);
  const [submissionPhase, setSubmissionPhase] =
    useState<SubmissionPhase>("idle");
  const [submitError, setSubmitError] = useState<ErrorPublic | null>(null);
  const [submitNetworkErrorMessage, setSubmitNetworkErrorMessage] = useState<
    string | null
  >(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [cueFilenamesById, setCueFilenamesById] = useState<
    Record<string, string>
  >({});
  // S0005: an additional, conditional advisory shown only alongside a
  // server `validation_error` from Analysis creation, and only when the
  // submitted request actually carried a non-blank Start/End value -- a
  // blank-trim `validation_error` is never attributable to trim bounds
  // (`specs/S0005-cue-trim-validation-clarity-and-analysis-creation-
  // regression/spec.md`).
  const [trimAdvisoryVisible, setTrimAdvisoryVisible] = useState(false);

  const polling = useAnalysisPolling(analysisId);
  const analysisActive = analysisId !== null;
  const isTerminal =
    polling.analysis?.status === "succeeded" ||
    polling.analysis?.status === "failed";

  const cueTimeParses = cueEntries.map((entry) => ({
    start: parseCueTimeText(entry.startText),
    end: parseCueTimeText(entry.endText),
  }));

  function crossFieldError(index: number): string | null {
    const { start, end } = cueTimeParses[index];
    if (start.kind === "value" && end.kind === "value" && start.seconds >= end.seconds) {
      return "Start time must be before end time.";
    }
    return null;
  }

  const hasLocalTimeError = cueEntries.some((_entry, index) => {
    const { start, end } = cueTimeParses[index];
    return (
      start.kind === "invalid" ||
      end.kind === "invalid" ||
      crossFieldError(index) !== null
    );
  });

  const canSubmit =
    !submitting &&
    analysisId === null &&
    sourceMediaFile !== null &&
    cueEntries.some((entry) => entry.file !== null) &&
    !hasLocalTimeError;

  function handleAddCue(): void {
    setCueEntries((entries) =>
      entries.length >= MAX_CUES ? entries : [...entries, newCueFileEntry()],
    );
  }

  function handleRemoveCue(key: string): void {
    setCueEntries((entries) =>
      entries.length <= 1 ? entries : entries.filter((entry) => entry.key !== key),
    );
  }

  function handleCueFileChange(key: string, file: File | null): void {
    setCueEntries((entries) =>
      entries.map((entry) => (entry.key === key ? { ...entry, file } : entry)),
    );
  }

  function handleCueFieldChange(
    key: string,
    field: "name" | "startText" | "endText",
    value: string,
  ): void {
    setCueEntries((entries) =>
      entries.map((entry) =>
        entry.key === key ? { ...entry, [field]: value } : entry,
      ),
    );
  }

  function handleSourceDrop(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setIsDraggingSource(false);
    if (analysisActive || submitting) {
      return;
    }
    const file = event.dataTransfer.files?.[0] ?? null;
    if (file) {
      setSourceMediaFile(file);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit || !sourceMediaFile) {
      return;
    }
    const activeCues = cueEntries.filter((entry) => entry.file !== null);
    if (activeCues.length === 0) {
      return;
    }

    setSubmitting(true);
    setSubmissionPhase("uploading");
    setSubmitError(null);
    setSubmitNetworkErrorMessage(null);
    setTrimAdvisoryVisible(false);

    try {
      const sourceUpload = await uploadSourceMedia(sourceMediaFile);
      if (!sourceUpload.ok) {
        setSubmitError(sourceUpload.error);
        return;
      }

      const cues: AnalysisCueReference[] = [];
      const filenamesById: Record<string, string> = {};
      let anyCueHasNonBlankTrimText = false;
      for (let index = 0; index < activeCues.length; index += 1) {
        const entry = activeCues[index];
        const cueUpload = await uploadCue(entry.file as File);
        if (!cueUpload.ok) {
          setSubmitError(cueUpload.error);
          return;
        }
        const cueId = `cue-${index + 1}`;
        const trimmedName = entry.name.trim();
        const startParse = parseCueTimeText(entry.startText);
        const endParse = parseCueTimeText(entry.endText);
        if (entry.startText.trim() !== "" || entry.endText.trim() !== "") {
          anyCueHasNonBlankTrimText = true;
        }
        cues.push({
          cue_id: cueId,
          asset_id: cueUpload.data.identifier,
          label: trimmedName === "" ? undefined : trimmedName,
          trim_start_seconds:
            startParse.kind === "value" ? startParse.seconds : null,
          trim_end_seconds: endParse.kind === "value" ? endParse.seconds : null,
        });
        filenamesById[cueId] = (entry.file as File).name;
      }

      setSubmissionPhase("locating");
      const analysisCreation = await createAnalysis(
        sourceUpload.data.identifier,
        cues,
        minimumSimilarityScoreRef.current,
      );
      if (!analysisCreation.ok) {
        setSubmitError(analysisCreation.error);
        // S0005: this advisory is conditional -- it never claims certainty
        // about the server's root cause, and it must not appear for a
        // blank-trim validation_error (the rejection must be something
        // else in that case).
        setTrimAdvisoryVisible(
          analysisCreation.error.error_code === "validation_error" &&
            anyCueHasNonBlankTrimText,
        );
        return;
      }

      setCueFilenamesById(filenamesById);
      setAnalysisId(analysisCreation.data.analysis_id);
    } catch (cause) {
      if (cause instanceof ApiNetworkError) {
        setSubmitNetworkErrorMessage(
          "Could not reach the server. Check your connection and try again.",
        );
        return;
      }
      throw cause;
    } finally {
      setSubmitting(false);
      setSubmissionPhase("idle");
    }
  }

  let lifecycleLabel = "Create Analysis";
  if (!analysisActive && submissionPhase === "uploading") {
    lifecycleLabel = "Uploading…";
  } else if (!analysisActive && submissionPhase === "locating") {
    lifecycleLabel = "Locating…";
  } else if (analysisActive && !isTerminal) {
    lifecycleLabel = "Locating…";
  }

  return (
    <>
      <section className="panel source-panel" aria-labelledby="source-title">
        <div className="panel-heading">
          <div className="heading-group">
            <span className="heading-icon heading-icon--purple" aria-hidden="true">
              <svg viewBox="0 0 24 24" role="img">
                <path d="M8 5.7v12.6L18.3 12 8 5.7Z" fill="currentColor" />
              </svg>
            </span>
            <div>
              <h2 id="source-title">Source media</h2>
              <p>Upload the media file you want to search through.</p>
            </div>
          </div>
          <div className="support-badge" aria-label="Audio and video supported">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
              <path d="M7 4v16M17 4v16M3 9h4M17 9h4M3 15h4M17 15h4" fill="none" stroke="currentColor" strokeWidth="1.6" />
            </svg>
            <span>
              Audio + Video
              <br />
              <strong>Supported!</strong>
            </span>
          </div>
        </div>

        <div
          className={`drop-zone${isDraggingSource ? " is-dragging" : ""}${
            sourceMediaFile ? " has-file" : ""
          }`}
          onDragOver={(event) => {
            event.preventDefault();
            if (!analysisActive && !submitting) {
              setIsDraggingSource(true);
            }
          }}
          onDragLeave={() => setIsDraggingSource(false)}
          onDrop={handleSourceDrop}
        >
          <div className="upload-cloud" aria-hidden="true">
            <svg viewBox="0 0 64 52">
              <path
                d="M19 45h31c7.2 0 13-5.7 13-12.8 0-6.6-5.1-12-11.6-12.7C49.1 10.1 41.2 3 31.6 3 20.8 3 12 11.8 12 22.6v.7C5.2 24.4 0 30.1 0 37c0 7.7 6.3 14 14 14h5"
                fill="currentColor"
                opacity=".85"
              />
              <path
                d="M32 38V19m0 0-8 8m8-8 8 8"
                fill="none"
                stroke="white"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="4"
              />
            </svg>
          </div>
          <strong>Drag and drop your media file here</strong>
          <span className="or-text">or</span>
          <label className="visually-hidden" htmlFor="source-media-input">
            Source media
          </label>
          <input
            id="source-media-input"
            className="visually-hidden"
            type="file"
            accept={SOURCE_ACCEPT}
            disabled={submitting || analysisActive}
            onChange={(event) =>
              setSourceMediaFile(event.target.files?.[0] ?? null)
            }
          />
          <button
            type="button"
            className="choose-file-button"
            disabled={submitting || analysisActive}
            onClick={() =>
              document.getElementById("source-media-input")?.click()
            }
          >
            Choose a file
          </button>
          <span className="selected-file" aria-live="polite">
            {sourceMediaFile?.name ?? ""}
          </span>
          <small>WAV, MP4/M4V, MOV, WebM, MKV, AVI</small>
        </div>
      </section>

      <section className="panel cues-panel" aria-labelledby="cues-title">
        <div className="panel-heading">
          <div className="heading-group">
            <span className="heading-icon heading-icon--mint" aria-hidden="true">
              ♫
            </span>
            <div>
              <h2 id="cues-title">Cues</h2>
              <p>Add one or more audio cues to search for in your media file.</p>
            </div>
          </div>
        </div>

        <form onSubmit={(event) => void handleSubmit(event)}>
          <div className="cue-list">
            {cueEntries.map((entry, index) => {
              const { start, end } = cueTimeParses[index];
              const startError = start.kind === "invalid";
              const endError = end.kind === "invalid";
              const crossError = crossFieldError(index);
              return (
                <article className="cue-row" key={entry.key}>
                  <div className="cue-identity">
                    <span
                      className={`cue-note${index % 2 === 1 ? " cue-note--alt" : ""}`}
                      aria-hidden="true"
                    >
                      ♫
                    </span>
                    <strong>Cue {index + 1}</strong>
                  </div>
                  <div className="cue-fields">
                    <input
                      id={`cue-input-${entry.key}`}
                      className="visually-hidden"
                      type="file"
                      accept="audio/wav,.wav"
                      aria-label={`Cue ${index + 1}`}
                      disabled={submitting || analysisActive}
                      onChange={(event) =>
                        handleCueFileChange(
                          entry.key,
                          event.target.files?.[0] ?? null,
                        )
                      }
                    />
                    <button
                      type="button"
                      className="file-picker-label file-picker-surface"
                      disabled={submitting || analysisActive}
                      onClick={() =>
                        document.getElementById(`cue-input-${entry.key}`)?.click()
                      }
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M7 3h7l4 4v14H7V3Zm7 0v5h5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                      </svg>
                      <span className="cue-file-text">
                        {entry.file ? entry.file.name : "Choose a cue file..."}
                      </span>
                    </button>
                    <small className="cue-file-help">WAV audio clip</small>

                    <div className="optional-fields">
                      <label>
                        <span>
                          Name <em>(optional)</em>
                        </span>
                        <input
                          type="text"
                          maxLength={80}
                          placeholder="e.g. Notification sound"
                          value={entry.name}
                          disabled={submitting || analysisActive}
                          onChange={(event) =>
                            handleCueFieldChange(entry.key, "name", event.target.value)
                          }
                        />
                      </label>
                      <label>
                        <span>
                          Start time <em>(optional)</em>
                        </span>
                        <input
                          type="text"
                          inputMode="numeric"
                          placeholder="e.g. 00:00:01"
                          className={startError || crossError ? "has-error" : undefined}
                          value={entry.startText}
                          disabled={submitting || analysisActive}
                          aria-invalid={startError || crossError !== null}
                          onChange={(event) =>
                            handleCueFieldChange(
                              entry.key,
                              "startText",
                              event.target.value,
                            )
                          }
                        />
                        {startError && (
                          <p className="field-error" role="alert">
                            Enter a start time as MM:SS or HH:MM:SS.
                          </p>
                        )}
                      </label>
                      <label>
                        <span>
                          End time <em>(optional)</em>
                        </span>
                        <input
                          type="text"
                          inputMode="numeric"
                          placeholder="e.g. 00:00:03"
                          className={endError || crossError ? "has-error" : undefined}
                          value={entry.endText}
                          disabled={submitting || analysisActive}
                          aria-invalid={endError || crossError !== null}
                          onChange={(event) =>
                            handleCueFieldChange(
                              entry.key,
                              "endText",
                              event.target.value,
                            )
                          }
                        />
                        {endError && (
                          <p className="field-error" role="alert">
                            Enter an end time as MM:SS or HH:MM:SS.
                          </p>
                        )}
                        {!startError && !endError && crossError && (
                          <p className="field-error" role="alert">
                            {crossError}
                          </p>
                        )}
                      </label>
                    </div>
                  </div>
                  {cueEntries.length > 1 && (
                    <button
                      type="button"
                      className="remove-cue-button"
                      aria-label={`Remove cue ${index + 1}`}
                      disabled={submitting || analysisActive}
                      onClick={() => handleRemoveCue(entry.key)}
                    >
                      ×
                    </button>
                  )}
                </article>
              );
            })}
          </div>

          <div className="cue-actions">
            <button
              type="button"
              className="add-cue-button"
              disabled={submitting || analysisActive || cueEntries.length >= MAX_CUES}
              onClick={handleAddCue}
            >
              <span className="plus-icon" aria-hidden="true">
                +
              </span>
              Add another cue
            </button>
            {cueEntries.length >= MAX_CUES && (
              <p className="cue-limit-note">Maximum of 20 cues reached.</p>
            )}

            <button
              type="submit"
              className="create-analysis-button"
              disabled={!canSubmit}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" strokeWidth="2.5" />
                <path d="m15.5 15.5 5 5" stroke="currentColor" strokeLinecap="round" strokeWidth="2.5" />
              </svg>
              <span>{lifecycleLabel}</span>
            </button>
            {!analysisActive && submissionPhase !== "idle" && (
              <ActivityIndicator phase={submissionPhase} />
            )}
          </div>
        </form>

        {submitError && <ApiErrorNotice error={submitError} />}
        {submitError && trimAdvisoryVisible && (
          <p role="status" className="form-alert">
            Start and End define the search window inside the source media.
            Check that the values form a valid interval within the source duration.
          </p>
        )}
        {submitNetworkErrorMessage && (
          <p role="alert" className="form-alert">
            {submitNetworkErrorMessage}
          </p>
        )}
      </section>

      {analysisId && (
        <ResultPage
          analysisId={analysisId}
          analysis={polling.analysis}
          pollingError={polling.error}
          connectionLost={polling.connectionLost}
          isPolling={polling.isPolling}
          cueFilenamesById={cueFilenamesById}
        />
      )}
    </>
  );
}
