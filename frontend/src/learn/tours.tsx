import { useState } from 'react';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
import { ChapterShell, Figure, Callout, FictionalTag, StatePill } from './ui';
import { FICTION } from './content';

/* ================= Tour 1 — How to review a candidate ================= */

const REVIEW_STEPS = [
  {
    title: 'Interpret the score with its range and confidence',
    body: 'Open Review on a saved candidate. Match score is at the top. Use it to choose where to look first. A high score with low confidence can rest on very little evidence; confidence measures evidence availability, not the probability of job success.',
    visual: 'score',
  },
  {
    title: 'Start with the criteria that matter most',
    body: 'Choose Review score evidence to reach Review against your criteria. For a backend role, start with the required skills and relevant experience. Matched means text was found. No exact match means the searched text did not match. Not checked means the evidence is incomplete. None of these alone is a hiring decision.',
    visual: 'criteria',
  },
  {
    title: 'Open the passage and check its context',
    body: 'Click a quoted passage. Its saved source opens directly underneath, with the exact text highlighted. Was this hands-on work, a course, or a passing mention? Which role and period does it refer to? Select I checked this passage against the criterion only if you checked the context. Leave it unchecked when unclear. The score does not change.',
    visual: 'source',
  },
  {
    title: 'Read the career behind the keywords',
    body: 'Continue to Career history. Look for relevant responsibilities, scope, and dates. For example, “built reporting services using PostgreSQL” supports hands-on usage but says nothing by itself about database architecture or scale. Use All saved text & score history to inspect the complete career text when the extracted entries are incomplete.',
    visual: 'extracted',
  },
  {
    title: 'Separate missing evidence from a mismatch',
    body: 'Check Evidence & downloads to see what is saved. A missing Skills section leaves a question open; it does not establish that a person lacks a skill. A location mismatch may need a conversation about relocation. Identify the specific gap before requesting more data.',
    visual: 'sections',
  },
  {
    title: 'Decide whether you need more',
    body: 'Open Download more profile information and request up to three relevant sections. Wait for retrieval to finish, then revisit the score and source evidence. New scoring inputs clear unrecorded checks so an old check is not applied to new evidence.',
    visual: 'request',
  },
  {
    title: 'Compare people and carry the open questions forward',
    body: 'Use Compare to select this person, then open Compare matches and select two or three people. Choose View comparison and read across the same criterion. Decide whose experience merits a conversation and what still needs asking. The optional Record source checks audit needs 10 distinct current passages; it is separate from candidate selection.',
    visual: 'criteria',
  },
] as const;

function StepVisual({ kind }: { kind: string }) {
  const [requested, setRequested] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [checked, setChecked] = useState(false);
  switch (kind) {
    case 'sections':
      return (
        <ul className="space-y-1.5 text-xs">
          {[['Main profile', 'Saved', 'sage'], ['Experience', 'Saved', 'sage'], ['Skills', 'Not requested', 'plain'], ['Education', 'Failed', 'rust']].map(([n, s, t]) => (
            <li key={n} className="flex items-center justify-between rounded-lg border border-line bg-canvas px-3 py-2">
              <span className="font-medium text-ink">{n}</span>
              <StatePill tone={t as 'sage' | 'plain' | 'rust' | 'amber'}>{s}</StatePill>
            </li>
          ))}
        </ul>
      );
    case 'extracted':
      return (
        <div className="rounded-lg border border-line bg-canvas p-3 text-xs">
          <p className="font-semibold text-ink">Backend Engineer — Example Metrics Co.</p>
          <p className="mt-0.5 text-faint">2021 — now · Go 4 yrs · PostgreSQL reporting services</p>
          <p className="mt-2 font-semibold text-ink">Senior Developer — Northwind</p>
          <p className="mt-0.5 text-faint">2019 — 2021</p>
        </div>
      );
    case 'source':
      return <div className="rounded-xl border border-line bg-canvas p-4 text-xs">
        <p className="font-medium text-ink">PostgreSQL <StatePill tone="sage">Matched</StatePill></p>
        <button aria-expanded={sourceOpen} onClick={() => setSourceOpen(!sourceOpen)} className="mt-3 w-full rounded-lg border border-line bg-surface p-3 text-left text-accent">“Built reporting services using PostgreSQL.”<span className="block mt-1 text-faint">Experience · {checked ? 'Source checked' : 'Open source to check'}</span></button>
        {sourceOpen ? <div className="mt-3 rounded-lg border border-line p-3">
          <p>Backend Engineer — Example Metrics Co. (2021 — now)</p>
          <p className="mt-2"><mark className="bg-amber-soft">Built reporting services using PostgreSQL.</mark> Maintained payment services in Go. The profile does not describe the database size or architecture responsibilities.</p>
          <label className="mt-3 flex gap-2 items-start"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />I checked this passage against the criterion</label>
          <p className="mt-2 text-faint">{checked ? 'Example source checked. Score unchanged.' : 'Hands-on usage is supported. Ask about scale and ownership in a conversation.'}</p>
        </div> : null}
      </div>;
    case 'criteria':
      return (
        <ul className="space-y-1.5 text-xs">
          {[['Go (required)', 'Matched', 'sage'], ['PostgreSQL (required)', 'Matched', 'sage'], ['Distributed systems', 'Not checked', 'plain'], ['Berlin or remote', 'No exact match', 'amber']].map(([n, s, t]) => (
            <li key={n} className="flex items-center justify-between rounded-lg border border-line bg-canvas px-3 py-2">
              <span className="font-medium text-ink">{n}</span>
              <StatePill tone={t as 'sage' | 'plain' | 'rust' | 'amber'}>{s}</StatePill>
            </li>
          ))}
        </ul>
      );
    case 'score':
      return (
        <div className="rounded-lg border border-line bg-canvas p-4">
          <div className="flex items-center gap-5">
            <div className="text-center"><p className="font-display text-2xl font-extrabold text-ink">89</p><p className="text-[9px] font-semibold uppercase tracking-wide text-faint">Score</p></div>
            <div className="text-center"><p className="font-display text-base font-bold text-body">67–92</p><p className="text-[9px] font-semibold uppercase tracking-wide text-faint">Range</p></div>
            <div className="text-center"><p className="font-display text-base font-bold text-accent">75%</p><p className="text-[9px] font-semibold uppercase tracking-wide text-faint">Confidence</p></div>
          </div>
          <div className="relative mt-3 h-2 rounded-full bg-subtle">
            <div className="absolute inset-y-0 rounded-full bg-amberdeep/30" style={{ left: '67%', width: '25%' }} />
            <div className="absolute top-1/2 h-3.5 w-1 -translate-y-1/2 rounded-full bg-accent" style={{ left: 'calc(89% - 2px)' }} />
          </div>
        </div>
      );
    default:
      return (
        <div className="rounded-lg border border-line bg-canvas p-3">
          <p className="text-xs font-medium text-ink">No supporting match was found in available text, and Skills is missing. Retrieving it may resolve the unknown; it does not guarantee a match.</p>
          <button onClick={() => setRequested(true)} disabled={requested} className="mt-2 rounded-full border border-line px-3 py-1.5 text-xs font-medium text-accent">{requested ? 'Example request queued — no live download' : 'Simulate requesting Skills'}</button>
        </div>
      );
  }
}

export function TourReview() {
  const [step, setStep] = useState(0);
  return (
    <ChapterShell
      chapterId="tour-review"
      kicker="Guided tour"
      title="How to review a candidate"
      intro="Use the candidate panel to answer three questions: what is relevant, what is supported, and what still needs asking. Compass checks saved text against your criteria — your review adds the context: what the person actually did, how relevant it is, and whether the source supports the displayed result."
    >
      <Figure caption={`A guided reading journey through one fictional candidate (${FICTION.candidate}). Step ${step + 1} of ${REVIEW_STEPS.length}: ${REVIEW_STEPS[step].title}.`}>
        <div className="flex flex-wrap gap-1.5">
          {REVIEW_STEPS.map((s, i) => (
            <button
              key={s.title}
              onClick={() => setStep(i)}
              className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold transition-colors ${
                i === step ? 'bg-accent text-white' : i < step ? 'bg-sage-soft text-sage' : 'bg-subtle text-faint'
              }`}
              aria-label={`Step ${i + 1}: ${s.title}`}
              aria-current={i === step ? 'step' : undefined}
            >
              {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
            </button>
          ))}
        </div>

        <div className="mt-5 grid items-start gap-5 sm:grid-cols-2">
          <div>
            <p className="flex flex-wrap items-start gap-2 font-display text-base font-bold text-ink">
              {REVIEW_STEPS[step].title} <FictionalTag />
            </p>
            <p className="mt-2 text-sm leading-relaxed text-body">{REVIEW_STEPS[step].body}</p>
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => setStep(Math.max(0, step - 1))}
                disabled={step === 0}
                className="inline-flex items-center gap-1.5 rounded-full border border-line px-4 py-2 text-xs font-medium text-body hover:border-accent disabled:opacity-40"
              >
                <ArrowLeft className="h-3.5 w-3.5" /> Back
              </button>
              <button
                onClick={() => setStep(Math.min(REVIEW_STEPS.length - 1, step + 1))}
                disabled={step === REVIEW_STEPS.length - 1}
                className="inline-flex items-center gap-1.5 rounded-full bg-ink px-4 py-2 text-xs font-semibold text-canvas hover:bg-accent-strong disabled:opacity-40"
              >
                Next step <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <StepVisual key={step} kind={REVIEW_STEPS[step].visual} />
        </div>
      </Figure>
    </ChapterShell>
  );
}

/* ================= Tour 2 — How to compare candidates ================= */

const COMPARE_GUIDANCE = [
  'Select two or three candidates in Compare matches and open the comparison.',
  'Start with required criteria — read across the same criterion for each person before jumping between overall scores.',
  'Distinguish evidence gaps from mismatches: “unknown” prompts investigation; it does not establish that someone lacks the qualification.',
  'Check evidence depth: a matching term means text was found — read the passage to judge its context and relevance.',
  'Consider information completeness: more downloaded sections support more conclusions. Read confidence and missing sections alongside the score.',
  'Inspect meaningful differences: open the profiles and source passages when a criterion could change your assessment.',
  'Review your priorities: if the ranking feels wrong for the role, inspect Search criteria and the scoring weights in Settings — saving changes triggers local rescoring.',
  'Identify what remains unresolved: separate supported observations from questions that need more information or a conversation.',
];

const WORKED = [
  {
    id: 'A',
    name: 'Candidate A',
    pill: 'Matched' as const,
    tone: 'sage' as const,
    headline: 'Saved text supports a match',
    passage: '“Built reporting services using PostgreSQL.”',
    explanation: 'The term appears in the saved experience text, so the criterion is matched. Your job: inspect the surrounding experience for relevance — reporting services may or may not be the kind of PostgreSQL work you need.',
  },
  {
    id: 'B',
    name: 'Candidate B',
    pill: 'Not matched' as const,
    tone: 'rust' as const,
    headline: 'Checked, with no match found',
    passage: 'The relevant saved sections were searched — no “PostgreSQL”, “Postgres”, or configured alias appears.',
    explanation: 'This is a real result, not a gap: the available information was checked. But it does not prove they have never used PostgreSQL — profiles are incomplete by nature.',
  },
  {
    id: 'C',
    name: 'Candidate C',
    pill: 'Unknown' as const,
    tone: 'plain' as const,
    headline: 'The needed section is missing',
    passage: 'No supporting match was found in available text, and a relevant section is missing. The full required coverage is not available.',
    explanation: 'No conclusion is possible yet. Retrieve the relevant sections before treating this as a weakness — unknown is a request for information, not an answer.',
  },
];

export function TourCompare() {
  const [open, setOpen] = useState('A');
  const current = WORKED.find((w) => w.id === open)!;

  return (
    <ChapterShell
      chapterId="tour-compare"
      kicker="Guided tour"
      title="How to compare candidates"
      intro="Compare people against the same requirements, one criterion at a time. Use the overall score to orient your review, then inspect the evidence behind the differences. A higher score means a stronger result under the current rules and saved information — it does not independently establish that someone is the better hire."
    >
      <section>
        <h2 className="font-display text-lg font-bold text-ink">The comparison sequence</h2>
        <ol className="mt-4 space-y-2.5">
          {COMPARE_GUIDANCE.map((g, i) => (
            <li key={i} className="flex gap-3 rounded-xl border border-line bg-surface px-4 py-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-bold text-accent">{i + 1}</span>
              <p className="text-sm leading-relaxed text-body">{g}</p>
            </li>
          ))}
        </ol>
      </section>

      <Figure caption="A worked example — requirement: PostgreSQL experience. Three fictional candidates, three meaningfully different situations. Select each person to see why “matched”, “not matched”, and “unknown” must not be read as the same thing. These examples explain interpretation; they are not real candidate results.">
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">Requirement under review: PostgreSQL experience</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          {WORKED.map((w) => (
            <button
              key={w.id}
              onClick={() => setOpen(w.id)}
              aria-pressed={open === w.id}
              className={`rounded-xl border p-3.5 text-left transition-all ${
                open === w.id ? 'border-accent bg-accent-soft shadow-sm' : 'border-line bg-canvas hover:border-accent/50'
              }`}
            >
              <p className="text-sm font-bold text-ink">{w.name}</p>
              <div className="mt-2"><StatePill tone={w.tone}>{w.pill}</StatePill></div>
              <p className="mt-2 text-[11px] leading-snug text-faint">{w.headline}</p>
            </button>
          ))}
        </div>
        <div className="fade-enter mt-4 rounded-xl border border-line bg-canvas p-4" key={open}>
          <p className="text-xs font-semibold text-ink">{current.name} — {current.headline} <FictionalTag /></p>
          <p className="mt-2 rounded-lg bg-subtle px-3 py-2 font-mono text-[12px] leading-relaxed text-body">{current.passage}</p>
          <p className="mt-3 text-sm leading-relaxed text-body">{current.explanation}</p>
        </div>
      </Figure>

      <Callout>
        When a criterion could change your decision, open the profile and read the passage. Scores orient; evidence decides what deserves a closer look.
      </Callout>
    </ChapterShell>
  );
}
