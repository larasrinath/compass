import { useState } from 'react';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
import { ChapterShell, Figure, Callout, FictionalTag, StatePill } from './ui';
import { FICTION } from './content';

/* ================= Tour 1 — How to review a candidate ================= */

const REVIEW_STEPS = [
  {
    title: 'Interpret the score with its range and confidence',
    body: 'Open Review on a candidate card. Match score and the signal table are at the top of the panel. The headline number orients you; the range tells you how uncertain it is; confidence tells you how much of the assessment rests on usable information. Never treat the number as a complete assessment.',
    visual: 'score',
  },
  {
    title: 'Read the criteria results',
    body: 'For each requirement, note which have supporting evidence, which were checked and not matched, and which remain unknown. These three states mean different things.',
    visual: 'criteria',
  },
  {
    title: 'Read the extracted profile details',
    body: 'Continue through Why they might fit, Worth checking, and Career history. Review experience, titles, employers, and the other available fields. This is Compass’s structured reading of the saved text.',
    visual: 'extracted',
  },
  {
    title: 'Check what was downloaded',
    body: 'Under Evidence & downloads, check the saved sections; Scoring & source details also lists Downloaded sections. Missing experience or skills sections limit what any comparison can tell you. An incomplete download is an incomplete view of the person.',
    visual: 'sections',
  },
  {
    title: 'Inspect the original text',
    body: 'Choose Review score evidence or View evidence to open Scoring & source details and the supporting passage. Ask: does the extracted detail accurately represent what the passage actually says?',
    visual: 'source',
  },
  {
    title: 'Decide whether you need more',
    body: 'If an important unknown could be resolved, request the relevant sections — up to three at a time — and review again once they arrive.',
    visual: 'request',
  },
] as const;

function StepVisual({ kind }: { kind: string }) {
  const [requested, setRequested] = useState(false);
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
      return (
        <div className="rounded-lg border border-line bg-canvas p-3 font-mono text-[11px] leading-relaxed">
          <p className="text-faint">Backend Engineer — Example Metrics Co. (2021 — now)</p>
          <p className="rounded bg-amber-soft px-1.5 py-0.5 text-ink outline outline-1 outline-amberdeep/40">Built reporting services using PostgreSQL.</p>
          <p className="text-faint">Maintained payment services in Go for four years.</p>
          <p className="mt-2 font-sans text-[10px] uppercase tracking-wide text-accent">↑ the passage behind the “PostgreSQL” field</p>
        </div>
      );
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
      intro="Follow the candidate panel from Match score through criteria, career history, saved sections, and source evidence. Compass checks saved text against your criteria — your review adds the context: what the person actually did, how relevant it is, and whether the source supports the displayed result."
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
  'Review your priorities: if the ranking feels wrong for the role, inspect the saved criteria and weights — changes trigger local rescoring.',
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
