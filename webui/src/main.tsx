import React, { useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";

import logoUrl from "./assets/audio-cue-locator-logo.svg";
import NewAnalysisPage from "./pages/NewAnalysisPage";
import "./styles.css";

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
  const helpButtonRef = useRef<HTMLButtonElement>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);

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

        <NewAnalysisPage />
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
        <p>
          The current standalone baseline exposes no user-configurable
          application settings in the WebUI; matching/resource policy is
          server-owned.
        </p>
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
