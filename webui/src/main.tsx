import React from "react";
import ReactDOM from "react-dom/client";

/**
 * The REST API v1 base namespace this WebUI is authorized to call
 * (docs/rest-api-v1-contract.md). M6-01 documents this boundary only;
 * no request is made against it yet. M6-02 introduces the first actual
 * API call (media/cue submission and Analysis lifecycle polling).
 */
const API_BASE_URL = "/api/v1";

function App(): JSX.Element {
  return (
    <main>
      <h1>Audio Cue Locator</h1>
      <p>
        Standalone WebUI scaffold. This view intentionally implements no
        media/cue submission, result presentation, or timeline
        visualization yet — those are introduced by M6-02 through M6-04.
      </p>
      <p>
        Intended REST API v1 base: <code>{API_BASE_URL}</code>
      </p>
    </main>
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
