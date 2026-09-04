// Colored band badges for the case-detail timeline. Band vocabularies are read from the real
// evidence-engine code (backend/app/domain/evidence_engine/{velocity,category_shift,
// clustering}.py), not guessed:
//   velocity:       normal | elevated | critical
//   category_shift: none | minor | significant | severe
//   clustering:     normal | clustered | highly_clustered
//
// velocity and clustering have three bands each, so they map 1:1 onto green/amber/red.
// category_shift has FOUR, and collapsing it into the same three colors made "minor" and
// "significant" render identically -- the one place the band scales genuinely differ. It now
// has its own four-step scale (human-approved 2026-09-04), so a "significant" shift is
// visually distinct from a "minor" one at a glance during the demo.
export type BadgeTone = "green" | "amber" | "orange" | "red" | "neutral";

// velocity: normal | elevated | critical -- clustering: normal | clustered | highly_clustered
const BAND_TONE: Record<string, BadgeTone> = {
  normal: "green",
  elevated: "amber",
  critical: "red",
  clustered: "amber",
  highly_clustered: "red",
};

export function toneForBand(band: string): BadgeTone {
  return BAND_TONE[band] ?? "neutral";
}

// category_shift: none | minor | significant | severe -- its own four-step scale.
const CATEGORY_SHIFT_TONE: Record<string, BadgeTone> = {
  none: "green",
  minor: "amber",
  significant: "orange",
  severe: "red",
};

export function toneForCategoryShift(band: string): BadgeTone {
  return CATEGORY_SHIFT_TONE[band] ?? "neutral";
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
