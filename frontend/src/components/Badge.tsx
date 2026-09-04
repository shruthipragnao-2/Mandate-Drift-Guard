// Colored band badges for the case-detail timeline. Band vocabularies are read from the real
// evidence-engine code (backend/app/domain/evidence_engine/{velocity,category_shift,
// clustering}.py), not guessed:
//   velocity:       normal | elevated | critical
//   category_shift: none | minor | significant | severe
//   clustering:     normal | clustered | highly_clustered
//
// The spec's 3-color rule (green=normal/none, amber=elevated/minor/clustered,
// red=critical/severe/highly_clustered) doesn't assign category_shift's 4th value,
// "significant", to any color -- it sits between "minor" and "severe" in the real band
// ordering. Bucketed here as amber (the same tier as "minor"), since with only 3 colors for
// 4 ordered bands, splitting green=1/amber=2/red=1 is the least arbitrary option -- flagged
// in this project's report-back rather than resolved silently.
const GREEN = new Set(["normal", "none"]);
const AMBER = new Set(["elevated", "minor", "significant", "clustered"]);
const RED = new Set(["critical", "severe", "highly_clustered"]);

export type BadgeTone = "green" | "amber" | "red" | "neutral";

export function toneForBand(band: string): BadgeTone {
  if (GREEN.has(band)) return "green";
  if (AMBER.has(band)) return "amber";
  if (RED.has(band)) return "red";
  return "neutral";
}

const RISK_TONE: Record<string, BadgeTone> = {
  low: "green",
  medium: "amber",
  high: "red",
};

export function toneForRisk(level: string): BadgeTone {
  return RISK_TONE[level] ?? "neutral";
}

// mandate_alignment is inverted from risk_level: "low" alignment means a POOR match (bad,
// red), "high" alignment means a good match (green) -- opposite polarity from risk_level's
// "low risk is good" despite sharing the same three words.
const ALIGNMENT_TONE: Record<string, BadgeTone> = {
  low: "red",
  medium: "amber",
  high: "green",
};

export function toneForAlignment(level: string): BadgeTone {
  return ALIGNMENT_TONE[level] ?? "neutral";
}

export function Badge({ label, tone }: { label: string; tone: BadgeTone }) {
  return <span className={`badge badge-${tone}`}>{label}</span>;
}
