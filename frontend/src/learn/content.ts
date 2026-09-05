/* Fictional, illustrative content for the Learn section.
   The scenario used throughout: hiring a backend engineer with Go and PostgreSQL experience. */

export interface ChapterMeta {
  id: string;
  group: string;
  title: string;
  blurb: string;
}

export const CHAPTERS: ChapterMeta[] = [
  { id: 'what-compass-does', group: 'The basics', title: 'What Compass does', blurb: 'The whole product in one picture: criteria, discovery, evidence, comparison.' },
  { id: 'set-up-role', group: 'The basics', title: 'Set up your role', blurb: 'Turn a plain-language description into criteria you control — with skills, locations and optional preferences.' },
  { id: 'discover-candidates', group: 'The basics', title: 'Discover candidates before downloading', blurb: 'Finding someone and saving their profile are two different things.' },
  { id: 'download-evidence', group: 'The basics', title: 'Download and inspect evidence', blurb: 'Saved sections, extracted details, and the original text behind them.' },
  { id: 'after-a-request', group: 'The basics', title: 'What happens after a request', blurb: 'Follow one click through the queue, the connector, and local matching.' },
  { id: 'review-and-compare', group: 'Working with results', title: 'Review the pool and compare people', blurb: 'Confirm your review to unlock side-by-side comparison.' },
  { id: 'scores-uncertainty', group: 'Working with results', title: 'Understand scores and uncertainty', blurb: 'Score, range, and confidence are three different things.' },
  { id: 'priorities-verify', group: 'Working with results', title: 'Change priorities and verify evidence', blurb: 'Adjust weights, rescore locally, and record what you have checked.' },
  { id: 'return-to-work', group: 'Working with results', title: 'Return to saved work', blurb: 'What still works when the connector is unavailable.' },
  { id: 'tour-review', group: 'Guided tours', title: 'How to review a candidate', blurb: 'A six-step reading journey through one fictional profile.' },
  { id: 'tour-compare', group: 'Guided tours', title: 'How to compare candidates', blurb: 'Read criterion-by-criterion — with a worked PostgreSQL example.' },
];

/* The running fictional candidate */
export const FICTION = {
  candidate: 'Robin Serrano',
  candidateNote: 'fictional example profile',
  headline: 'Backend Engineer at Example Metrics Co.',
  role: 'Backend engineer with Go and PostgreSQL experience',
  searches: ['“Go backend engineer, Berlin”', '“PostgreSQL platform engineer, remote EU”'],
};

export interface CriterionResult {
  name: string;
  weight: number;
  state: 'matched' | 'not-matched' | 'unknown';
  note: string;
}

export const SCORE_CRITERIA: CriterionResult[] = [
  { name: 'Go (required skill)', weight: 3, state: 'matched', note: '“Maintained payment services in Go for four years.” — experience section' },
  { name: 'PostgreSQL (required skill)', weight: 3, state: 'matched', note: '“Built reporting services using PostgreSQL” — experience section' },
  { name: 'Distributed systems (optional skill)', weight: 2, state: 'unknown', note: 'No match in available text; relevant Skills section is missing' },
  { name: '5+ years experience', weight: 2, state: 'matched', note: 'Saved experience supports the five-year minimum' },
  { name: 'Berlin or remote (EU)', weight: 1, state: 'not-matched', note: 'Location field reads “Lisbon, on-site only”' },
  { name: 'Fintech industry', weight: 1, state: 'unknown', note: 'Employer industry could not be parsed reliably' },
];

export function computeScore(criteria: CriterionResult[], decimalPlaces = 0) {
  const total = criteria.reduce((s, c) => s + c.weight, 0);
  if (total === 0 || criteria.length === 0) return { scored: false as const, reason: 'No active criteria' };
  const matched = criteria.filter((c) => c.state === 'matched').reduce((s, c) => s + c.weight, 0);
  const usable = criteria.filter((c) => c.state !== 'unknown').reduce((s, c) => s + c.weight, 0);
  if (usable === 0) return { scored: false as const, reason: 'Active criteria lack evidence' };
  const round = (value: number) => Math.round(value * 10 ** decimalPlaces) / 10 ** decimalPlaces;
  const score = round((matched / usable) * 100);
  const confidence = Math.round((usable / total) * 100);
  return {
    scored: true as const,
    score,
    confidence,
    low: round((matched / total) * 100),
    high: round(((matched + total - usable) / total) * 100),
  };
}

export const VERIFY_PASSAGES = [
  { id: 'v1', claim: 'Go (2019–2023)', passage: '“Built and maintained payment services in Go (2019–2023).”' },
  { id: 'v2', claim: 'PostgreSQL', passage: '“Built reporting services using PostgreSQL.”' },
  { id: 'v3', claim: 'Led a team of four engineers', passage: '“Led a team of four engineers through a migration.”' },
  { id: 'v4', claim: '2019 — 2021', passage: '“Example Metrics Co. 2021 — now · Northwind 2019 — 2021.”' },
  { id: 'v5', claim: 'Kubernetes', passage: '“Deployed services on Kubernetes across three environments.”' },
  { id: 'v6', claim: 'deployment pipeline', passage: '“Owned the deployment pipeline used by 12 engineers.”' },
  { id: 'v7', claim: 'Designed the public REST API', passage: '“Designed the public REST API consumed by mobile clients.”' },
  { id: 'v8', claim: 'coverage from 40% to 85%', passage: '“Raised coverage from 40% to 85% on core services.”' },
  { id: 'v9', claim: 'On-call', passage: '“Rotated on-call for payment services, 99.95% uptime.”' },
  { id: 'v10', claim: 'Mentored two junior engineers', passage: '“Mentored two junior engineers to promotion.”' },
  { id: 'v11', claim: 'Kafka', passage: '“Moved event processing to Kafka, 2M events/day.”' },
  { id: 'v12', claim: 'Redis', passage: '“Introduced Redis caching; cut p95 latency by 40%.”' },
];

// Signal-level teaching fixture matching the working results table and weights editor.
// Binary examples deliberately omit partial signals and penalties.
export const RESULT_SIGNALS: (CriterionResult & { id: string })[] = [
  { id: 'S-1', name: 'Required skills', weight: 30, state: 'matched', note: 'Saved experience mentions Go and PostgreSQL.' },
  { id: 'S-2', name: 'Optional skills', weight: 10, state: 'unknown', note: 'Skills has not been retrieved; distributed systems remains unchecked.' },
  { id: 'S-3', name: 'Experience depth', weight: 20, state: 'matched', note: 'Saved dates support the five-year minimum.' },
  { id: 'S-4', name: 'Title similarity', weight: 12, state: 'matched', note: 'Backend Engineer appears in the saved headline.' },
  { id: 'S-5', name: 'Industry relevance', weight: 10, state: 'unknown', note: 'Employer industry could not be interpreted reliably.' },
  { id: 'S-6', name: 'Location fit', weight: 8, state: 'not-matched', note: 'Saved location is Lisbon; the brief asks for Berlin.' },
  { id: 'S-8', name: 'Required credentials', weight: 0, state: 'unknown', note: 'No credential is specified in this example brief.' },
];
