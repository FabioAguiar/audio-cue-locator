import React, { useState } from "react";
import ReactDOM from "react-dom/client";

import NewAnalysisPage from "./pages/NewAnalysisPage";
import ResultPage from "./pages/ResultPage";

function App(): JSX.Element {
  const [resultAnalysisId, setResultAnalysisId] = useState<string | null>(null);

  return (
    <main>
      <h1>Audio Cue Locator</h1>
      {resultAnalysisId === null ? (
        <NewAnalysisPage
          onViewResults={(analysisId) => setResultAnalysisId(analysisId)}
        />
      ) : (
        <ResultPage analysisId={resultAnalysisId} />
      )}
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
