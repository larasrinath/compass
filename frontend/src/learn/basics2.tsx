import { useEffect, useRef, useState } from 'react';
import { ArrowDownUp, ArrowRight, Download, FileText, Play, RotateCcw } from 'lucide-react';
import { ChapterShell, Figure, Callout, DownArrow, FictionalTag, StatePill } from './ui';
import { FICTION } from './content';

/* ================= Chapter 4 — Download and inspect evidence ================= */

type SectionState = 'saved' | 'not-requested' | 'failed' | 'rate-limited' | 'unparsable' | 'queued';

const SECTION_REASONS: Record<string, string> = {
  saved: 'Downloaded and stored.',
  'not-requested': 'You have not requested this section yet.',
  failed: 'Retrieval failed — you can retry.',
  'rate-limited': 'LinkedIn slowed requests; the task paused.',
  unparsable: 'Retrieved, but the text could not be interpreted reliably.',
  queued: 'Waiting in the queue.',
};

const SECTION_STYLE: Record<SectionState, 'sage' | 'plain' | 'rust' | 'amber'> = {
  saved: 'sage',
  'not-requested': 'plain',
  failed: 'rust',
  'rate-limited': 'amber',
  unparsable: 'amber',
  queued: 'amber',
};

const SOURCE_LINES = [
  { id: 'l1', text: 'Backend Engineer — Example Metrics Co. (2021 — now)', link: null },
  { id: 'l2', text: 'Built reporting services using PostgreSQL.', link: 'field-postgres' },
  { id: 'l3', text: 'Maintained payment services in Go for four years.', link: 'field-go' },
  { id: 'l4', text: 'Led a team of four engineers through a migration.', link: 'field-lead' },
  { id: 'l5', text: 'Senior Developer — Northwind (2019 — 2021)', link: null },
];

const EXTRACTED = [
  { id: 'field-postgres', label: 'PostgreSQL', value: 'reporting services', source: 'l2' },
  { id: 'field-go', label: 'Go', value: 'Maintained payment services in Go for four years.', source: 'l3' },
  { id: 'field-lead', label: 'Source passage', value: 'Led a team of four engineers through a migration.', source: 'l4' },
];

export function Ch4() {
  const [sections, setSections] = useState<Record<string, SectionState>>({
    'Main profile': 'saved',
    Experience: 'saved',
    Skills: 'not-requested',
    Education: 'failed',
    Certifications: 'rate-limited',
    Projects: 'unparsable',
  });
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach(window.clearTimeout), []);
  const [highlight, setHighlight] = useState<string | null>('l2');

  const queuedCount = Object.values(sections).filter((s) => s === 'queued').length;
  const requestSection = (name: string) => {
    if (queuedCount >= 3) return;
    setSections((prev) => ({ ...prev, [name]: 'queued' }));
    timers.current.push(window.setTimeout(() => setSections((prev) => ({ ...prev, [name]: 'saved' })), 2000));
  };

  return (
    <ChapterShell
      chapterId="download-evidence"
      kicker="The basics · 4 of 9"
      title="Download and inspect evidence"
      intro="With automatic downloads enabled in Settings, Compass retrieves the main profile and experience for each new person. From the candidate detail you can request up to three more sections at a time. Every extracted detail stays connected to the original source text — select an example evidence link to highlight its source. These links illustrate provenance, not every field the parser can extract. A missing section always tells you why."
    >
      <Figure caption="One fictional profile, three connected views: the saved source text (left), the illustrative evidence links (right), and the highlighted passage linking them. Select any extracted field to jump to its source. Below, each section shows an explicit state — including why something is missing.">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-line bg-canvas p-4">
            <p className="flex flex-wrap items-center gap-2 text-xs font-bold uppercase tracking-wide text-faint">
              <FileText className="h-3.5 w-3.5" /> Saved source text <FictionalTag />
            </p>
            <div className="mt-3 space-y-1 font-mono text-[12px] leading-relaxed">
              {SOURCE_LINES.map((l) => (
                <p
                  key={l.id}
                  className={`rounded px-2 py-1 transition-colors ${
                    highlight === l.id ? 'bg-amber-soft outline outline-1 outline-amberdeep/40 text-ink' : 'text-body'
                  }`}
                >
                  {l.text}
                </p>
              ))}
            </div>
          </div>
          <div className="rounded-xl border border-line bg-canvas p-4">
            <p className="text-xs font-bold uppercase tracking-wide text-faint">Illustrative evidence links</p>
            <p className="mt-2 text-sm font-semibold text-ink">{FICTION.candidate} — {FICTION.headline}</p>
            <div className="mt-3 space-y-2">
              {EXTRACTED.map((f) => (
                <button
                  key={f.id}
                  onClick={() => setHighlight(f.source)}
                  aria-pressed={highlight === f.source}
                  className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors ${
                    highlight === f.source ? 'border-accent bg-accent-soft' : 'border-line bg-surface hover:border-accent/50'
                  }`}
                >
                  <div>
                    <p className="text-xs font-semibold text-ink">{f.label}</p>
                    <p className="text-xs text-faint">{f.value}</p>
                  </div>
                  <span className="inline-flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-accent">View source <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" /></span>
                </button>
              ))}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-faint">
              The extracted fields never float free of the original — each one points back to the passage that supports it.
            </p>
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-line bg-canvas p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-bold uppercase tracking-wide text-faint">Profile sections</p>
            <p className="text-[11px] text-faint">Teaching simulation: {queuedCount} section(s) queued; real requests select up to 3 sections together</p>
          </div>
          <ul className="mt-3 divide-y divide-line">
            {Object.entries(sections).map(([name, state]) => (
              <li key={name} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                <div>
                  <p className="text-sm font-medium text-ink">{name}</p>
                  <p className="text-[11px] text-faint">{SECTION_REASONS[state]}</p>
                </div>
                <div className="flex items-center gap-2">
                  <StatePill tone={SECTION_STYLE[state]}>
                    {state === 'saved' ? 'Saved' : state === 'not-requested' ? 'Not requested' : state === 'failed' ? 'Failed' : state === 'rate-limited' ? 'Rate limited' : state === 'queued' ? 'In queue' : 'Couldn’t parse'}
                  </StatePill>
                  {(state === 'not-requested' || state === 'failed') && (
                    <button
                      onClick={() => requestSection(name)}
                      disabled={queuedCount >= 3}
                      className="inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-[11px] font-medium text-accent transition-colors hover:border-accent disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <Download className="h-3 w-3" /> Request
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </Figure>

      <Callout>
        A section can be missing for four different reasons — not requested, retrieval failed, rate limited, or unparsable. A parse problem may leave the raw source available even when a structured result is unknown. A rate-limited request needs queue-level resume after the cause is resolved; completed sections are retained. Each reason is shown explicitly, because “we never asked” and “the information doesn’t exist” are very different things.
      </Callout>
    </ChapterShell>
  );
}

/* ================= Chapter 5 — What happens after a request ================= */

const FLOW_STEPS = [
  { label: 'Your click', detail: 'You run a search. If automatic downloads are enabled in Settings, this also queues its new profiles for download. For an older search, select Download remaining profiles. You can separately request more sections for a saved candidate; opening Review does not request extra sections.' },
  { label: 'Waiting in queue', detail: 'The local service records the task. Profile downloads can overlap in one or two separate tabs, depending on Settings and connector capacity; search and other operations run exclusively. Tasks wait when capacity is full, between paced reads, or during a retry delay. A paused queue needs your attention before retrieval continues.' },
  { label: 'Local LinkedIn connector', detail: 'The worker sends the request to the LinkedIn connector started by ./compass. It opens LinkedIn pages using its existing signed-in browser session. This is browser automation, not a bulk profile API; a hidden tab does not make a profile visit anonymous. On first use, the launcher opens a LinkedIn sign-in window. You enter your credentials there; Compass reuses the session on later launches.' },
  { label: 'Response saved', detail: 'The complete response is committed to local storage before profile information is extracted. Previously saved sections are retained if a later request fails or is rate limited.' },
  { label: 'Details extracted', detail: 'Local parsers identify fields such as job title, company and dates. Extracted evidence points back to the saved section and exact source text; a parsing gap does not erase the original response.' },
  { label: 'Local matching', detail: 'Saved evidence is checked against the current role criteria and scoring weights. Missing sections stay distinguishable from searched text with no exact match. Rescoring saved profiles does not contact LinkedIn.' },
  { label: 'Result shown', detail: 'The interface updates retrieval status and available evidence. After you review the candidate list, you can inspect scores, open source checks and compare people. Opening evidence does not automatically verify it.' },
];

const PARTS = [
  { name: 'Interface', does: 'Receives your actions and displays results.' },
  { name: 'Local service', does: 'Coordinates retrieval, extraction, and scoring.' },
  { name: 'Local database', does: 'Keeps searches, profiles, evidence, and history.' },
  { name: 'LinkedIn connector', does: 'The only part that reaches out to LinkedIn.' },
];

export function Ch5() {
  const [step, setStep] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach(window.clearTimeout), []);

  const play = () => {
    if (playing) return;
    setPlaying(true);
    setStep(0);
    FLOW_STEPS.forEach((_, i) => {
      timers.current.push(window.setTimeout(() => {
        setStep(i);
        if (i === FLOW_STEPS.length - 1) setPlaying(false);
      }, i * 900));
    });
  };

  return (
    <ChapterShell
      chapterId="after-a-request"
      kicker="The basics · 5 of 9"
      title="What happens after a request"
      intro="Every search or download becomes a queued task. The queue uses the concurrency and pacing saved in Settings, within the separate connector’s capacity. Each response is saved before it becomes candidate information, and matching runs locally against saved profiles and your current criteria."
    >
      <Figure caption="A profile download, traced end to end. The connector runs locally; its retrieval request crosses the network boundary to LinkedIn. Search results populate the pool first; automatic profile downloads follow, and each saved response updates the score. Press play to watch a request move through the queue.">
        <div className="flex justify-center">
          <button
            onClick={play}
            disabled={playing}
            className="inline-flex items-center gap-2 rounded-full bg-ink px-5 py-2.5 text-sm font-semibold text-canvas hover:bg-accent-strong disabled:opacity-50"
          >
            {step === FLOW_STEPS.length - 1 ? <RotateCcw className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            {playing ? 'Running…' : step === FLOW_STEPS.length - 1 ? 'Replay' : 'Play a request'}
          </button>
        </div>

        <p className="mt-4 text-sm text-faint">Read every step below, select one to highlight it, or play the sequence.</p>
        <p className="sr-only" aria-live="polite">{step >= 0 ? `Step ${step + 1}: ${FLOW_STEPS[step].label}` : 'Ready to play the request.'}</p>
        <ol className="learn-flow mt-5" aria-label="Request steps">
          {FLOW_STEPS.map((item, index) => <li key={item.label}>
            <button type="button" className="learn-flow-card" disabled={playing} aria-pressed={step === index} aria-label={`Step ${index + 1}: ${item.label}`} onClick={() => setStep(index)}>
              <span className="learn-flow-number" aria-hidden="true">{index + 1}</span>
              <span className="learn-flow-content">
                <span className="learn-flow-title">{item.label}</span>
                <span className="learn-flow-description">{item.detail}</span>
                {index === 2 && <span className="learn-flow-network"><ArrowDownUp aria-hidden="true" className="mt-0.5 h-4 w-4" /><span>Network request and response: the local connector exchanges data with LinkedIn. The following storage, extraction and matching steps happen on your computer.</span></span>}
              </span>
            </button>
            {index < FLOW_STEPS.length - 1 && <DownArrow />}
          </li>)}
        </ol>
      </Figure>

      <section>
        <h2 className="font-display text-lg font-bold text-ink">Control downloads in Settings</h2>
        <p className="mt-3 text-sm leading-relaxed text-body">Settings separates how Compass retrieves profiles from who you want to find. Keep skills, locations, company and network filters in Search criteria.</p>
        <dl className="mt-4 divide-y divide-line rounded-2xl border border-line bg-surface px-5">
          {[
            ['Downloads', 'Turn automatic profile downloads on or off, choose one or two profiles at a time, and set the pause between reads.'],
            ['Search batches', 'Set new downloads per batch and maximum search pages, or turn off continuing through pages. The defaults are 1,000 each; saved profiles can keep accumulating across batches.'],
            ['Retry timing', 'Set delays for eligible busy-connector and timeout retries. These controls do not remove rate-limit cooldowns or resume a paused queue.'],
          ].map(([label, detail]) => <div key={label} className="py-4"><dt className="text-sm font-medium text-ink">{label}</dt><dd className="mt-1 text-sm leading-relaxed text-faint">{detail}</dd></div>)}
        </dl>
        <p className="mt-3 text-sm leading-relaxed text-body">Choose Save settings. Concurrency and pacing apply to upcoming reads; running reads finish. Batch limits and automatic retrieval choices apply to new batches and searches. Existing queued downloads remain queued. Scoring weights have a separate save action below these controls and recalculate locally.</p>
      </section>

      <section>
        <h2 className="font-display text-lg font-bold text-ink">Who is responsible for what</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {PARTS.map((p) => (
            <div key={p.name} className="rounded-xl border border-line bg-surface p-4">
              <p className="text-sm font-semibold text-ink">{p.name}</p>
              <p className="mt-1 text-xs leading-relaxed text-faint">{p.does}</p>
            </div>
          ))}
        </div>
      </section>

      <Callout>
        Because the raw response is saved first, you can inspect stored source text where it is safe to display; sensitive passages may be withheld — extraction never overwrites the source.
      </Callout>
    </ChapterShell>
  );
}
