import React from "react";

export type ActivityPhase = "uploading" | "locating";

interface ActivityIndicatorProps {
  phase: ActivityPhase;
}

const activityLabels: Record<ActivityPhase, string> = {
  uploading: "Uploading media…",
  locating: "Locating cues…",
};

/**
 * Presentation-only, indeterminate activity feedback for the submission
 * workflow. Network requests and persisted Analysis lifecycle state remain
 * owned by the page and polling hook respectively; this component only
 * renders the current phase.
 */
export default function ActivityIndicator({
  phase,
}: ActivityIndicatorProps): JSX.Element {
  return (
    <div
      className={`acl-activity acl-activity--${phase}`}
      data-activity-phase={phase}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div className="acl-activity__visual" aria-hidden="true">
        <div className="acl-activity__bars">
          {Array.from({ length: 10 }, (_value, index) => (
            <span className="acl-activity__bar" key={index} />
          ))}
        </div>

        {phase === "uploading" ? (
          <svg
            className="acl-activity__upload-glyph"
            viewBox="0 0 32 32"
            focusable="false"
          >
            <rect x="4" y="20" width="24" height="8" rx="4" />
            <path d="M16 21V5m0 0-6 6m6-6 6 6" />
          </svg>
        ) : (
          <svg
            className="acl-activity__scanner"
            viewBox="0 0 36 36"
            focusable="false"
          >
            <circle cx="15" cy="15" r="9" />
            <path d="m22 22 9 9" />
          </svg>
        )}
      </div>
      <span className="acl-activity__label">{activityLabels[phase]}</span>
    </div>
  );
}
