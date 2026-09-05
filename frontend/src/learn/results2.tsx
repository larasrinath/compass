import { useMemo, useState } from 'react';
import { ArrowRight, BadgeCheck, CloudOff, Globe, Play, RotateCcw } from 'lucide-react';
import { ChapterShell, Figure, Callout, FictionalTag, StatePill } from './ui';
import { FICTION, RESULT_SIGNALS, VERIFY_PASSAGES, computeScore } from './content';

/* ================= Chapter 8 — Change priorities and verify evidence ================= */

export function Ch8() {
  const [draftWeights, setDraftWeights] = useState(() => RESULT_SIGNALS.map((c) => c.weight));
  const [committed, setCommitted] = useState(() => RESULT_SIGNALS.map((c) => ({ ...c })));
  const [previousScore, setPreviousScore] = useState<number | null>(null);
  const [verified, setVerified] = useState<string[]>([]);
  const [accepted, setAccepted] = useState(false);
  const [verificationNote, setVerificationNote] = useState('');

  const committedResult = useMemo(() => computeScore(committed, 1), [committed]);
  const dirty = draftWeights.some((w, i) => w !== committed[i].weight);

  const saveWeights = () => {
    if (committedResult.scored) setPreviousScore(committedResult.score);
    setVerified([]);
    setAccepted(false);
    setCommitted(RESULT_SIGNALS.map((c, i) => ({ ...c, weight: draftWeights[i] })));
  };

  const toggleVerify = (id: string) => {
    setAccepted(false);
    setVerified((prev) => (prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id]));
  };

  return (
    <ChapterShell
      chapterId="priorities-verify"
      kicker="Working with results · 8 of 9"
      title="Change priorities and verify evidence"
      intro="Open Settings, then Scoring weights. Edit a signal’s numeric weight and choose Save scoring weights. Saving new weights — or saving changes to the brief — recalculates every result locally, using the same saved evidence. To verify a source, open a candidate, choose Review score evidence, open a quoted passage under Review against your criteria, and select I checked this passage against the criterion after reading its highlighted source. Return to Compare matches and expand Record source checks to record at least 10 distinct links for current scores."
    >
      <Figure caption="The same saved evidence, scored under your priorities. Edit a number and save — the recalculation is local and instant, and the candidate keeps the previous score for reference. Nothing is re-downloaded.">
        <div className="rounded-xl border border-line bg-canvas p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm font-semibold text-ink">{FICTION.candidate} <FictionalTag /></p>
            <div className="flex items-center gap-4 text-sm">
              {previousScore !== null && <span className="text-faint">Previous score: <strong className="text-body">{previousScore}</strong></span>}
              {committedResult.scored && (
                <span>Current score: <strong className="font-display text-lg text-accent">{committedResult.score}</strong>
                  <span className="text-faint"> (range {committedResult.low}–{committedResult.high})</span>
                </span>
              )}
              {!committedResult.scored && <p className="text-xs text-faint">Not scored — {committedResult.reason}</p>}
            </div>
          </div>
          <section className="mt-4 rounded-2xl border border-line bg-surface p-5" aria-label="Example scoring settings"><h2 className="text-base font-medium text-ink">Scoring weights</h2><div className="mt-4 space-y-3">
            {RESULT_SIGNALS.map((c, i) => (
              <div key={c.name} className="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-2">
                <p className="text-xs font-medium text-ink">{c.id} · {c.name}</p>
                <input
                  type="number"
                  min={0}
                  step={1}
                  disabled={c.id === 'S-8'}
                  value={draftWeights[i]}
                  onChange={(e) => {
                    const next = [...draftWeights];
                    next[i] = Math.max(0, Number(e.target.value));
                    setDraftWeights(next);
                  }}
                  className="w-20 rounded-xl border border-line bg-surface px-3 py-2 text-sm"
                  aria-label={`${c.name} weight`}
                />
                {c.id === 'S-8' && <span className="col-span-2 text-xs text-faint">Saved, not currently applied: brief input is empty.</span>}
              </div>
            ))}
          </div>
          <div className="mt-4 flex items-center justify-between border-t border-line pt-4">
            <p className="text-xs text-faint">{dirty ? 'Unsaved changes — results update when you save.' : 'Scoring is up to date.'}</p>
            <button
              onClick={saveWeights}
              disabled={!dirty}
              className="rounded-full bg-ink px-5 py-2.5 text-sm font-semibold text-canvas hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-40"
            >
              Save scoring weights
            </button>
          </div></section>
        </div>
      </Figure>

      <Figure caption="The evidence-quality check is separate from comparing — it is not required to open a comparison. On the working pages, each source check appears directly below the highlighted saved passage in Review against your criteria. This example collects them together for practice. Read each passage and record the check once 10 distinct current evidence links are verified. Saving scoring changes clears this example’s verification selection. This is an optional audit record, not a prerequisite for comparison or a candidate approval. Unrecorded selections clear on reload or when scoring inputs change. Search context and records of past checks don’t count.">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-bold uppercase tracking-wide text-faint">Record source checks</p>
          <StatePill tone={verified.length >= 10 ? 'sage' : 'plain'}>
            {accepted ? 'Example check recorded' : `${verified.length} of 10 verified`}
          </StatePill>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {VERIFY_PASSAGES.map((v) => (
            <label
              key={v.id}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                verified.includes(v.id) ? 'border-sage/40 bg-sage-soft/60' : 'border-line bg-canvas hover:border-accent/40'
              }`}
            >
              <input
                type="checkbox"
                checked={verified.includes(v.id)}
                onChange={() => toggleVerify(v.id)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[#2F7A57]"
              />
              <span>
                <span className="block text-xs font-medium text-ink">{v.claim}</span>
                <span className="block text-xs text-body">I checked this passage against the criterion</span>
                <span className="block text-[11px] leading-relaxed text-faint">{v.passage}</span>
              </span>
            </label>
          ))}
        </div>
        <label className="mt-4 block text-sm text-body">Verification note
          <textarea value={verificationNote} onChange={(e) => setVerificationNote(e.target.value)} className="mt-2 block w-full rounded-xl border border-line p-3" rows={2} />
        </label>
        <button onClick={() => setAccepted(true)} disabled={verified.length < 10 || !verificationNote.trim() || accepted} className="mt-3 rounded-full bg-ink px-4 py-2 text-sm text-canvas disabled:opacity-40">{accepted ? 'Example check recorded' : 'Record checks'}</button>
      </Figure>

      <Callout>
        Recalculating changes how saved evidence is weighed — it never refreshes the underlying profile. New information only arrives when you request it from LinkedIn.
      </Callout>
    </ChapterShell>
  );
}

/* ================= Chapter 9 — Return to saved work ================= */

export function Ch9() {
  const [connectorUp, setConnectorUp] = useState(true);
  const [rescored, setRescored] = useState(false);
  const [queued, setQueued] = useState(false);
  const [paused, setPaused] = useState(true);
  return (
    <ChapterShell chapterId="return-to-work" kicker="Working with results · 9 of 9" title="Return to saved work"
      intro="Open Saved searches from the sidebar. Each search shows up to three saved profiles: Review opens a candidate, and Open results returns to that search’s full candidate pool. Saved searches, downloaded profiles, evidence and score history survive application restarts. New searches and downloads need the connector. Saved browsing and rescoring work without it, as long as the local application is running. Temporary comparison picks and unchecked review selections are not durable records.">
      <Figure caption="Saved searches shows a three-profile preview. Open results opens the full pool, filtered to that search. Review opens one saved candidate. All interactions below stay inside this fictional example.">
        <SavedWorkExample />
      </Figure>
      <Figure caption="This simulation separates connector availability from local analysis. It does not change the actual connector or saved data. Returning online does not automatically resume work that requires your attention.">
        <button aria-pressed={connectorUp} onClick={() => setConnectorUp(!connectorUp)} className="rounded-full border border-line px-4 py-3 text-sm"><Globe className="mr-2 inline h-4 w-4" />Example connector: {connectorUp ? 'Available' : 'Unavailable'} — toggle</button>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="rounded-xl border border-line p-4">
            <h2 className="font-semibold">Saved information</h2><p className="mt-2 text-sm">Robin’s saved profile and evidence remain available in either connection state.</p>
            <button onClick={() => setRescored(true)} className="mt-3 rounded-full border border-line px-3 py-2 text-sm"><RotateCcw className="mr-1 inline h-4 w-4" />Recalculate example</button>
            {rescored && <p role="status" className="mt-2 text-sm text-sage"><BadgeCheck className="mr-1 inline h-4 w-4" />Recalculated locally; profile unchanged.</p>}
          </div>
          <div className="rounded-xl border border-line p-4">
            <h2 className="font-semibold"><CloudOff className="mr-1 inline h-4 w-4" />New retrieval</h2><p className="mt-2 text-sm">Needs a reachable connector and an active queue.</p>
            <button disabled={!connectorUp || paused || queued} onClick={() => setQueued(true)} className="mt-3 rounded-full border border-line px-3 py-2 text-sm disabled:opacity-40">{queued ? 'Example task queued' : 'Simulate a download'}</button>
            <p role="status" className="mt-2 text-xs">{!connectorUp ? 'Start the connector and use Check connection in the dashboard.' : paused ? 'Resolve the pause below before requesting more work.' : 'Ready for a new request.'}</p>
          </div>
        </div>
        <div className="mt-4 rounded-xl border border-line p-4">
          <h2 className="font-semibold">Queue recovery</h2>
          <p className="mt-2 text-sm">{paused ? 'Example: a rate limit paused downloads. Previously saved sections remain intact. After the indicated wait and resolving the cause, explicitly resume the queue.' : 'Example queue resumed. A profile continuation requests only its missing sections.'}</p>
          <button disabled={!connectorUp || !paused} onClick={() => setPaused(false)} className="mt-3 rounded-full border border-line px-3 py-2 text-sm disabled:opacity-40"><Play className="mr-1 inline h-4 w-4" />Simulate resume after waiting</button>
          <p className="mt-3 text-xs text-faint">The dashboard can cancel pending tasks and resume the queue; it does not expose per-task Pause buttons. Some timeout or browser-busy reads get one automatic retry. Interrupted work is not silently replayed.</p>
        </div>
      </Figure>
      <Callout>Recalculating never refreshes a LinkedIn profile. For a connection issue, start the separate connector, sign in there if needed, then use Check connection. For a rate limit, follow the queue’s suggested wait. Failed retrieval preserves previously saved data.</Callout>
    </ChapterShell>
  );
}

function SavedWorkExample() {
  const people = ['Robin Serrano', 'Marta Voss', 'Elif Demir', 'Jamie Chen', 'Alex Morgan'];
  const [view, setView] = useState<'saved' | 'results' | 'profile'>('saved');
  const [person, setPerson] = useState(people[0]);
  if (view === 'profile') return <div className="rounded-2xl border border-line bg-canvas p-5">
    <button onClick={() => setView('saved')} className="text-sm text-accent">Back to saved searches</button>
    <h2 className="mt-4 text-lg font-semibold text-ink">{person}</h2><p className="text-sm text-body">Backend Engineer · Profile saved locally</p>
    <h3 className="mt-4 text-sm font-medium">Review against your criteria</h3><p className="mt-1 text-sm text-faint">Saved experience mentions Go and PostgreSQL.</p>
    <h3 className="mt-4 text-sm font-medium">Evidence & downloads</h3><p className="mt-1 text-sm text-faint">Profile overview · Saved profile text</p>
  </div>;
  return <div className="overflow-hidden rounded-2xl border border-line bg-surface">
    {view === 'saved' ? <button onClick={() => setView('results')} className="flex w-full items-center justify-between gap-4 border-b border-line bg-canvas p-5 text-left">
      <span><span className="block text-base font-semibold text-ink">Go backend engineer</span><span className="mt-1 block text-xs text-faint">Berlin · 5 profile references · last run Sep 4, 2026</span></span><span className="inline-flex shrink-0 items-center gap-2 text-sm text-accent">Open results <ArrowRight aria-hidden="true" className="h-4 w-4" /></span>
    </button> : <div className="border-b border-line p-5"><button onClick={() => setView('saved')} className="text-sm text-accent">Back to saved searches</button><h2 className="mt-3 text-base font-semibold">Candidate pool</h2><p className="mt-1 text-xs text-faint">Results from: Go backend engineer · 5 shown · first-seen order</p></div>}
    <ul className="px-5">{(view === 'saved' ? people.slice(0, 3) : people).map(name => <li key={name} className="flex items-center justify-between gap-4 border-b border-line py-4 last:border-0">
      <div><p className="text-sm font-medium text-ink">{name}</p><p className="mt-1 text-xs text-faint">Profile saved · available offline</p></div><button aria-label={`Review ${name}`} onClick={() => { setPerson(name); setView('profile'); }} className="text-sm text-accent">Review</button>
    </li>)}</ul>
  </div>;
}
