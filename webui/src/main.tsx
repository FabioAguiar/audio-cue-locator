import React, { useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";

import logoUrl from "./assets/audio-cue-locator-logo.svg";
import NewAnalysisPage from "./pages/NewAnalysisPage";
import "./styles.css";

const RECOMMENDED_MINIMUM_SIMILARITY_SCORE = 0.71;
const MINIMUM_SIMILARITY_STORAGE_KEY =
  "audio-cue-locator.minimum-similarity-score.v1";

function loadMinimumSimilarityScore(): number | null {
  const stored = window.localStorage.getItem(MINIMUM_SIMILARITY_STORAGE_KEY);
  if (stored === null) {
    return null;
  }
  try {
    const value: unknown = JSON.parse(stored);
    if (
      typeof value === "number" &&
      Number.isFinite(value) &&
      value >= 0 &&
      value <= 1
    ) {
      return value;
    }
  } catch {
    // Invalid browser-local preferences fail closed to the server default.
  }
  window.localStorage.removeItem(MINIMUM_SIMILARITY_STORAGE_KEY);
  return null;
}

/**
 * Single persistent Home shell (S0004,
 * `specs/S0004-responsive-single-screen-home-design-convergence/spec.md`).
 *
 * Replaces the former two-view submission/Result switch: `NewAnalysisPage`
 * now owns the whole workflow -- Source media, Cues, lifecycle, and the
 * conditional Results card -- as one screen. This shell only renders the
 * utility actions (Help/Settings) and the brand block above it.
 */

interface InfoDialogProps {
  titleId: string;
  title: string;
  open: boolean;
  onRequestClose: () => void;
  returnFocusRef: React.RefObject<HTMLButtonElement>;
  children: React.ReactNode;
}

function InfoDialog({
  titleId,
  title,
  open,
  onRequestClose,
  returnFocusRef,
  children,
}: InfoDialogProps): JSX.Element {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  function handleClose(): void {
    onRequestClose();
    returnFocusRef.current?.focus();
  }

  return (
    <dialog
      ref={dialogRef}
      className="app-dialog"
      aria-labelledby={titleId}
      onClose={handleClose}
    >
      <button
        type="button"
        className="dialog-close"
        aria-label={`Close ${title}`}
        onClick={handleClose}
      >
        ×
      </button>
      <h2 id={titleId}>{title}</h2>
      <div>{children}</div>
      <button type="button" className="dialog-ok" onClick={handleClose}>
        Close
      </button>
    </dialog>
  );
}

function App(): JSX.Element {
  const [helpOpen, setHelpOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [minimumSimilarityScore, setMinimumSimilarityScore] = useState<
    number | null
  >(loadMinimumSimilarityScore);
  const helpButtonRef = useRef<HTMLButtonElement>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);

  const displayedMinimumSimilarityScore =
    minimumSimilarityScore ?? RECOMMENDED_MINIMUM_SIMILARITY_SCORE;

  function handleMinimumSimilarityScoreChange(
    event: React.ChangeEvent<HTMLInputElement>,
  ): void {
    const value = Number(event.currentTarget.value);
    setMinimumSimilarityScore(value);
    window.localStorage.setItem(
      MINIMUM_SIMILARITY_STORAGE_KEY,
      JSON.stringify(value),
    );
  }

  function handleMinimumSimilarityScoreReset(): void {
    setMinimumSimilarityScore(null);
    window.localStorage.removeItem(MINIMUM_SIMILARITY_STORAGE_KEY);
  }

  return (
    <div className="page-shell">
      <header className="utility-bar" aria-label="Utility navigation">
        <button
          ref={helpButtonRef}
          className="utility-button"
          type="button"
          aria-haspopup="dialog"
          onClick={() => setHelpOpen(true)}
        >
          <span className="utility-icon" aria-hidden="true">
            ?
          </span>
          <span className="utility-label">Help</span>
        </button>
        <button
          ref={settingsButtonRef}
          className="utility-button"
          type="button"
          aria-haspopup="dialog"
          onClick={() => setSettingsOpen(true)}
        >
          <span className="utility-icon utility-icon--gear" aria-hidden="true">
            ⚙
          </span>
          <span className="utility-label">Settings</span>
        </button>
      </header>

      <main className="app-frame">
        <section className="brand-block" aria-labelledby="home-title">
          <img className="brand-logo" src={logoUrl} alt="Audio Cue Locator" />
          <h1 id="home-title">Locate audio cues inside your media files</h1>
          <p>
            Upload a supported source file, add one or more WAV audio cues,
            and let ACL find the matches for you.
          </p>
        </section>

        <NewAnalysisPage minimumSimilarityScore={minimumSimilarityScore} />
      </main>

      <InfoDialog
        titleId="help-dialog-title"
        title="Help"
        open={helpOpen}
        onRequestClose={() => setHelpOpen(false)}
        returnFocusRef={helpButtonRef}
      >
        <ol>
          <li>Choose supported source media.</li>
          <li>Add one or more WAV Cues.</li>
          <li>Optionally name/trim Cues.</li>
          <li>Create Analysis.</li>
          <li>Review Results/download JSON.</li>
        </ol>
      </InfoDialog>

      <InfoDialog
        titleId="settings-dialog-title"
        title="Settings"
        open={settingsOpen}
        onRequestClose={() => setSettingsOpen(false)}
        returnFocusRef={settingsButtonRef}
      >
        <section
          className="similarity-setting"
          aria-labelledby="minimum-similarity-label"
        >
          <div className="similarity-setting-heading">
            <label
              id="minimum-similarity-label"
              htmlFor="minimum-similarity-score"
            >
              Minimum similarity score
            </label>
            <output htmlFor="minimum-similarity-score">
              {displayedMinimumSimilarityScore.toFixed(2)}
            </output>
          </div>
          <input
            id="minimum-similarity-score"
            className="similarity-range"
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={displayedMinimumSimilarityScore}
            aria-describedby="minimum-similarity-guidance minimum-similarity-mode"
            onChange={handleMinimumSimilarityScoreChange}
          />
          <div className="similarity-range-bounds" aria-hidden="true">
            <span>0.00</span>
            <span>1.00</span>
          </div>
          <p id="minimum-similarity-mode" className="similarity-setting-mode">
            {minimumSimilarityScore === null
              ? "Using the server recommended default (displayed as 0.71)."
              : "Custom value. Recommended default: 0.71."}
          </p>
          <p id="minimum-similarity-guidance" className="similarity-setting-help">
            Lower values can return more and less-similar matches. Higher values
            are stricter. The selected value is captured for each new Analysis.
          </p>
          <button
            type="button"
            className="similarity-reset"
            onClick={handleMinimumSimilarityScoreReset}
            disabled={minimumSimilarityScore === null}
          >
            Reset to recommended default
          </button>
        </section>
      </InfoDialog>
    </div>
  );
}

const container = document.getElementById("root");
if (container) {
  ReactDOM.createRoot(container).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
