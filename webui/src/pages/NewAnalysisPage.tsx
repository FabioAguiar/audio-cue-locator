import React, { useState } from "react";

import {
  ApiNetworkError,
  AnalysisCueReference,
  ErrorPublic,
  createAnalysis,
  uploadCue,
  uploadSourceMedia,
} from "../api/client";
import { useAnalysisPolling } from "../hooks/useAnalysisPolling";

/**
 * Submission screen for source media and one or more cues, and Analysis
 * creation (M6-02, `intents/M6/M6-02/implementation-handoff.json`).
 *
 * Scope, exactly as authorized:
 * - Upload one source-media file and one or more cue files through the
 *   two bounded upload routes.
 * - Create an Analysis from the resulting Asset identities and display
 *   its lifecycle via `useAnalysisPolling`.
 * - Present every upload/validation/creation error using only the shared
 *   `error_code`/`message`/`correlation_id` fields; never reformulate or
 *   append internal detail.
 *
 * Explicitly out of scope: presenting occurrences, scores, or the
 * downloadable Result JSON (M6-03), and any temporal/timeline
 * visualization (M6-04). A `succeeded` Analysis is acknowledged here
 * without fetching or rendering its Result body.
 *
 * M6-06 mounts this component as the application's entry view and uses
 * `onViewResults` to carry the server-issued Analysis identity into the
 * existing result view after lifecycle polling reports success.
 */

export interface NewAnalysisPageProps {
  onViewResults: (analysisId: string) => void;
}

interface CueFileEntry {
  /** Stable client-side key for React list rendering; distinct from the
   * `cue_id` sent to the API. */
  key: string;
  file: File | null;
}

let nextCueKey = 1;

function newCueFileEntry(): CueFileEntry {
  const entry = { key: `cue-${nextCueKey}`, file: null };
  nextCueKey += 1;
  return entry;
}

function ApiErrorNotice({ error }: { error: ErrorPublic }): JSX.Element {
  return (
    <p role="alert">
      {error.message} (error_code: {error.error_code}, correlation_id:{" "}
      {error.correlation_id})
    </p>
  );
}

export default function NewAnalysisPage({
  onViewResults,
}: NewAnalysisPageProps): JSX.Element {
  const [sourceMediaFile, setSourceMediaFile] = useState<File | null>(null);
  const [cueEntries, setCueEntries] = useState<CueFileEntry[]>([
    newCueFileEntry(),
  ]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ErrorPublic | null>(null);
  const [submitNetworkErrorMessage, setSubmitNetworkErrorMessage] = useState<
    string | null
  >(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);

  const polling = useAnalysisPolling(analysisId);

  const canSubmit =
    !submitting &&
    analysisId === null &&
    sourceMediaFile !== null &&
    cueEntries.some((entry) => entry.file !== null);

  function handleAddCue(): void {
    setCueEntries((entries) => [...entries, newCueFileEntry()]);
  }

  function handleRemoveCue(key: string): void {
    setCueEntries((entries) => entries.filter((entry) => entry.key !== key));
  }

  function handleCueFileChange(key: string, file: File | null): void {
    setCueEntries((entries) =>
      entries.map((entry) => (entry.key === key ? { ...entry, file } : entry)),
    );
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!sourceMediaFile) {
      return;
    }
    const cueFiles = cueEntries
      .map((entry) => entry.file)
      .filter((file): file is File => file !== null);
    if (cueFiles.length === 0) {
      return;
    }

    setSubmitting(true);
    setSubmitError(null);
    setSubmitNetworkErrorMessage(null);

    try {
      const sourceUpload = await uploadSourceMedia(sourceMediaFile);
      if (!sourceUpload.ok) {
        setSubmitError(sourceUpload.error);
        return;
      }

      const cues: AnalysisCueReference[] = [];
      for (let index = 0; index < cueFiles.length; index += 1) {
        const cueUpload = await uploadCue(cueFiles[index]);
        if (!cueUpload.ok) {
          setSubmitError(cueUpload.error);
          return;
        }
        cues.push({
          cue_id: `cue-${index + 1}`,
          asset_id: cueUpload.data.identifier,
        });
      }

      const analysisCreation = await createAnalysis(
        sourceUpload.data.identifier,
        cues,
      );
      if (!analysisCreation.ok) {
        setSubmitError(analysisCreation.error);
        return;
      }

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
    }
  }

  return (
    <section>
      <h2>Submit media and cues</h2>
      <form onSubmit={(event) => void handleSubmit(event)}>
        <div>
          <label htmlFor="source-media-input">Source media</label>
          <input
            id="source-media-input"
            type="file"
            accept="audio/wav,video/mp4"
            disabled={submitting || analysisId !== null}
            onChange={(event) =>
              setSourceMediaFile(event.target.files?.[0] ?? null)
            }
          />
        </div>

        <fieldset>
          <legend>Cues</legend>
          {cueEntries.map((entry, index) => (
            <div key={entry.key}>
              <label htmlFor={`cue-input-${entry.key}`}>Cue {index + 1}</label>
              <input
                id={`cue-input-${entry.key}`}
                type="file"
                accept="audio/wav"
                disabled={submitting || analysisId !== null}
                onChange={(event) =>
                  handleCueFileChange(entry.key, event.target.files?.[0] ?? null)
                }
              />
              {cueEntries.length > 1 && (
                <button
                  type="button"
                  disabled={submitting || analysisId !== null}
                  onClick={() => handleRemoveCue(entry.key)}
                >
                  Remove cue
                </button>
              )}
            </div>
          ))}
          <button
            type="button"
            disabled={submitting || analysisId !== null}
            onClick={handleAddCue}
          >
            Add another cue
          </button>
        </fieldset>

        <button type="submit" disabled={!canSubmit}>
          {submitting ? "Submitting…" : "Submit and create Analysis"}
        </button>
      </form>

      {submitError && <ApiErrorNotice error={submitError} />}
      {submitNetworkErrorMessage && (
        <p role="alert">{submitNetworkErrorMessage}</p>
      )}

      {analysisId && (
        <section aria-live="polite">
          <h3>Analysis {analysisId}</h3>
          {polling.error && <ApiErrorNotice error={polling.error} />}
          {polling.connectionLost && (
            <p role="alert">
              Lost the connection while checking Analysis status. The
              Analysis may still be in progress on the server.
            </p>
          )}
          {polling.analysis && (
            <p>
              Status: <strong>{polling.analysis.status}</strong>
              {polling.analysis.status === "failed" &&
                polling.analysis.structured_error && (
                  <>
                    {" "}
                    ({polling.analysis.structured_error.category}:{" "}
                    {polling.analysis.structured_error.message})
                  </>
                )}
            </p>
          )}
          {polling.analysis?.status === "succeeded" && (
            <button type="button" onClick={() => onViewResults(analysisId)}>
              View results
            </button>
          )}
        </section>
      )}
    </section>
  );
}
