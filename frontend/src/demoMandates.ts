// Hardcoded picker, deliberately NOT fetched from an API -- there is no GET /mandates
// endpoint (out of Checkpoint C14's scope), so this is the one place the frontend knows these
// ids exist at all. Populate this by running scripts/seed_demo_mandates.py from backend/ and
// pasting its printed output below. Re-running the seed script inserts a NEW set of mandates
// with fresh ids (the model defaults id to uuid4) -- this file will then be stale until
// updated by hand. The values below are real, from an actual run against the live dev DB.

export interface DemoMandate {
  id: string;
  purpose: string;
  allowed_categories: string[];
}

export const DEMO_MANDATES: DemoMandate[] = [
  {
    id: "24ffda43-692f-4602-ba58-056492ed1af2",
    purpose: "weekly household groceries",
    allowed_categories: ["groceries", "household essentials"],
  },
  {
    id: "0c4e511f-74ef-44e7-95c1-ccbbfd51c00b",
    purpose: "monthly utility bill payments",
    allowed_categories: ["bills", "telephone"],
  },
  {
    id: "8efac359-0fca-4065-8fd7-bf05601a759d",
    purpose: "monthly fuel and commute expenses",
    allowed_categories: ["fuel"],
  },
  {
    id: "34b5d984-fc78-4d7b-bb90-162dde12313e",
    purpose: "monthly house help and domestic staff wages",
    allowed_categories: ["house help", "household essentials"],
  },
];
