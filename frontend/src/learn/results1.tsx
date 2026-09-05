import { useMemo, useState } from 'react';
import { Lock, LockOpen, RefreshCw } from 'lucide-react';
import { ChapterShell, Figure, Callout, FictionalTag, StatePill } from './ui';
import { FICTION, RESULT_SIGNALS, computeScore, type CriterionResult } from './content';

/* ================= Chapter 6 — Review the pool and compare ================= */

const POOL = ['Robin Serrano', 'Marta Voss', 'Elif Demir'];

const COMPARE_ROWS: { criterion: string; results: ('matched' | 'not-matched' | 'unknown')[] }[] = [
  { criterion: 'Go (required)', results: ['matched', 'matched', 'unknown'] },
  { criterion: 'PostgreSQL (required)', results: ['matched', 'not-matched', 'unknown'] },
  { criterion: '5+ years', results: ['matched', 'matched', 'matched'] },
  { criterion: 'Berlin or remote (EU)', results: ['not-matched', 'matched', 'matched'] },
];

const RESULT_LABEL = { matched: 'Matched', 'not-matched': 'No exact match', unknown: 'Not checked' } as const;
const RESULT_TONE = { matched: 'sage', 'not-matched': 'amber', unknown: 'plain' } as const;

export function Ch6() {
  const [note, setNote] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [reloadCount, setReloadCount] = useState(0);
  const [showComparison, setShowComparison] = useState(false);

  const canConfirm = note.trim().length > 0;

  const toggleSelect = (name: string) => {
    setShowComparison(false);
    setSelected((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : prev.length < 3 ? [...prev, name] : prev));
  };

  const simulateReload = () => {
    setSelected([]);
    setShowComparison(false);
    setReloadCount((n) => n + 1);
  };

  return (
    <ChapterShell
      chapterId="review-and-compare"
      kicker="Working with results · 6 of 9"
      title="Review the pool and compare people"
      intro="Start by checking who is in the list. Then save the profiles worth investigating and review them against the role. Finally, compare two or three people on the same criteria. A list check, a candidate assessment, and a source check answer different questions."
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Callout><strong>Is this the right person?</strong><p>In Find candidates, check names, LinkedIn links, and repeated source searches. Add a note under Check candidate list and choose Confirm list &amp; show ranking. You stay on Find candidates.</p></Callout>
        <Callout><strong>What makes them relevant?</strong><p>Save profile, then open Review. Read the score with confidence, check the key criteria, and use career history to understand what they actually did.</p></Callout>
        <Callout><strong>Does the source support it?</strong><p>Open a passage under Review against your criteria. Read the highlighted text in context before checking it. This records your source check without changing the score.</p></Callout>
      </div>
      <Figure caption="The gate is deliberate: inspect the saved candidate list and add a note to unlock comparison. There is no per-person review checkbox in the connected dashboard. Try confirming with the note empty — it stays locked. The “reload page” button demonstrates that the comparison selection is temporary to the session.">
        <p className="text-xs font-bold uppercase tracking-wide text-faint">Find candidates · Candidate pool</p>
        <ul className="mt-3 space-y-2">
          {POOL.map((p) => (
            <li key={p} className="flex items-center justify-between gap-3 rounded-lg border border-line bg-canvas px-4 py-3">
              <p className="text-sm font-medium text-ink">{p} <FictionalTag /></p>

            </li>
          ))}
        </ul>
        <h2 className="mt-4 text-sm font-medium text-ink">Check names and duplicates</h2><label htmlFor="example-inspection-note" className="mt-3 block text-sm text-body">What did you check?</label>
        <textarea id="example-inspection-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-label="What did you check?"
          disabled={confirmed}
          rows={2}
          placeholder="For example: checked names and LinkedIn links; repeated results refer to the same people."
          className="mt-3 w-full rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink placeholder:text-faint/70 focus:border-accent focus:outline-none"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <p className="text-xs text-faint">
            {confirmed ? 'Review recorded for this example.' : !note.trim() ? 'Add a short note to finish.' : 'Ready to confirm.'}
          </p>
          <button
            onClick={() => setConfirmed(true)}
            disabled={!canConfirm || confirmed}
            className="inline-flex items-center gap-2 rounded-full bg-ink px-4 py-2 text-xs font-semibold text-canvas hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-40"
          >
            {confirmed ? <LockOpen className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
            {confirmed ? 'Comparison unlocked' : 'Confirm list & show ranking'}
          </button>
        </div>

        {confirmed && !compareOpen ? <div className="mt-4 rounded-xl border border-line p-4">
          <p className="text-sm text-ink">Find candidates · Ranked list</p><p className="mt-1 text-xs text-faint">List check recorded. Open saved profiles to assess the evidence before choosing people to compare.</p>
          <ol className="mt-3 space-y-2 text-sm">{POOL.map((name, i) => <li key={name}>{i + 1}. {name} · {[89, 82, 76][i]} / 100 · {[75, 90, 65][i]}% confidence</li>)}</ol>
          <button className="mt-3 rounded-full border border-line px-4 py-2 text-accent" onClick={() => setCompareOpen(true)}>Compare candidates</button>
        </div> : null}
        {compareOpen ? <div className={`mt-5 rounded-xl border p-4 transition-all ${confirmed ? 'border-line bg-canvas' : 'border-dashed border-line bg-subtle/40 opacity-70'}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-bold uppercase tracking-wide text-faint">Compare matches</p>
            <button onClick={simulateReload} className="inline-flex items-center gap-1 text-[11px] font-medium text-faint hover:text-body">
              <RefreshCw className="h-3 w-3" /> Simulate page reload {reloadCount > 0 && `(cleared ${reloadCount}×)`}
            </button>
          </div>
          {!confirmed ? (
            <p className="mt-2 text-sm text-faint">Locked until you confirm the review above.</p>
          ) : (
            <div className="fade-enter mt-3">
              <div className="grid gap-3 sm:grid-cols-2">
                {POOL.map((p, index) => <article key={p} className="rounded-2xl border border-line bg-surface p-4">
                  <h3 className="text-sm font-semibold text-ink">{p}</h3><p className="mt-1 text-xs text-faint">Backend Engineer · Profile and extra sections saved</p>
                  <p className="mt-4 text-xs text-faint">EVIDENCE FOUND</p><p className="mt-1 text-xs text-accent">{index === 2 ? 'Experience depth' : 'Go'}</p>
                  <p className="mt-3 text-xs text-faint">WORTH CHECKING</p><p className="mt-1 text-xs text-body">{index === 0 ? 'Location fit: no exact match in saved text.' : 'Required skills: review the source.'}</p>
                  <label className="mt-4 inline-flex cursor-pointer items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-body"><input type="checkbox" aria-label={`Compare ${p}`} checked={selected.includes(p)} onChange={() => toggleSelect(p)} className="h-3.5 w-3.5 accent-[#33615A]" />{selected.includes(p) ? 'Selected' : 'Compare'}</label>
                </article>)}
              </div>
              {selected.length > 0 && <button onClick={() => setShowComparison(true)} disabled={selected.length < 2} className="mt-3 rounded-full border border-line px-4 py-2 text-sm text-accent disabled:opacity-40">View comparison ({selected.length}/3)</button>}
              {showComparison && selected.length >= 2 ? (
                <div className="fade-enter mt-4 overflow-x-auto rounded-xl border border-line bg-surface">
                  <h2 className="px-4 pt-4 text-base font-semibold text-ink">Compare the evidence</h2><p className="px-4 py-2 text-xs text-faint">The same criteria, side by side. Open a profile to check the source.</p>
                  <table className="w-full min-w-[480px] text-left text-xs">
                    <thead>
                      <tr className="border-b border-line">
                        <th className="px-4 py-3 font-semibold uppercase tracking-wide text-faint">Criterion</th>
                        {selected.map((s) => (
                          <th key={s} className="px-4 py-3 font-semibold text-ink">{s}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {COMPARE_ROWS.map((row) => (
                        <tr key={row.criterion} className="border-b border-line last:border-0">
                          <td className="px-4 py-2.5 font-medium text-ink">{row.criterion}</td>
                          {selected.map((s) => {
                            const idx = POOL.indexOf(s);
                            const r = row.results[idx];
                            return (
                              <td key={s} className="px-4 py-2.5">
                                <StatePill tone={RESULT_TONE[r]}>{r === 'matched' ? 'Evidence found' : RESULT_LABEL[r]}</StatePill><p className="mt-2 text-faint">{r === 'matched' ? `Saved profile mentions ${row.criterion}.` : r === 'not-matched' ? 'No matching text found in the checked sections.' : 'More source evidence is needed.'}</p>
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="mt-3 text-sm text-faint">{selected.length >= 2 ? 'Choose View comparison to open the table.' : 'Select 2–3 people to compare.'}</p>
              )}
              <p className="mt-3 text-[11px] leading-relaxed text-faint">
                This example deliberately includes matched, missing, and unknown evidence — real comparisons are rarely complete.
              </p>
            </div>
          )}
        </div> : null}
      </Figure>

      <Callout>
        The profile’s signal table uses Matched, No exact match, Not checked, Conflicting evidence, and Partial match. The comparison table calls a match Evidence found. Inspect individual criteria and their evidence — not just the overall order. The list note records an identity check, not a hiring decision. Source checks do not certify someone’s qualifications. Use the evidence to decide what to explore in a conversation.
      </Callout>
    </ChapterShell>
  );
}

/* ================= Chapter 7 — Scores and uncertainty ================= */

export function Ch7() {
  const [criteria, setCriteria] = useState<CriterionResult[]>(RESULT_SIGNALS);
  const result = useMemo(() => computeScore(criteria, 1), [criteria]);

  const flip = (name: string, state: CriterionResult['state']) =>
    setCriteria((prev) => prev.map((c) => (c.name === name ? { ...c, state, note: 'Teaching scenario changed manually; no profile data was changed.' } : c)));

  return (
    <ChapterShell
      chapterId="scores-uncertainty"
      kicker="Working with results · 7 of 9"
      title="Understand scores and uncertainty"
      intro="Expand Scoring details on a result card, or open Review to see Match score and the signal table at the top of the candidate panel. Review score evidence takes you to Review against your criteria. Open a quoted passage to read its saved source and check it in place."
    >
      <Figure caption="Three separate concepts: the score (how strongly usable information matched), the range (bounds when unknowns are unresolved), and confidence (how much of the assessment rests on usable information). This simplified binary example uses the dashboard’s score and bounds logic, but omits partial signals and penalties. Change a result, resolve missing information, or clear the criteria.">
        <div className="rounded-xl border border-line bg-canvas p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-ink">{FICTION.candidate} <FictionalTag /></p>
              <p className="text-xs text-faint">{FICTION.headline}</p>
            </div>
          </div>
          <h2 className="mt-5 text-base font-semibold text-ink">Match score</h2>
          {result.scored ? <div className="mt-2 flex flex-wrap items-center justify-between gap-4">
            <div><p className="font-display text-3xl font-semibold text-accent">{result.score.toFixed(1)}</p><p className="text-xs text-body">Range {result.low.toFixed(1)}–{result.high.toFixed(1)}</p></div>
            <div><span className="rounded-full border border-line px-3 py-1.5 text-xs text-body">{result.confidence >= 80 ? 'high' : result.confidence >= 50 ? 'medium' : 'low'} confidence · {result.confidence}%</span><p className="mt-2 text-xs text-faint">Confidence reflects available evidence.</p></div>
          </div> : <p className="mt-2 text-sm text-faint">{result.reason === 'No active criteria' ? 'Not scored — no active scoring criteria' : 'not found in the retrieved data · active criteria lack evidence'}</p>}
          <p className="mt-5 text-xs text-body">How retrieved evidence contributes to this score</p>
          <div className="mt-2 overflow-x-auto"><table className="w-full min-w-[500px] text-left text-xs">
            <thead><tr className="border-b border-line">{['Signal', 'Result', 'Weight', 'Contribution', 'Availability'].map(label => <th className="px-2 py-3 font-medium text-faint" key={label}>{label}</th>)}</tr></thead>
            <tbody>{criteria.filter(c => c.weight > 0).map(c => <tr className="border-b border-line" key={c.name}><th className="px-2 py-3 font-medium">{RESULT_SIGNALS.find(signal => signal.name === c.name)?.id} · {c.name}</th><td className="px-2 py-3"><StatePill tone={RESULT_TONE[c.state]}>{RESULT_LABEL[c.state]}</StatePill></td><td className="px-2 py-3">{c.weight}</td><td className="px-2 py-3">{(c.state === 'matched' ? c.weight : 0).toFixed(1)}</td><td className="px-2 py-3">{c.state === 'unknown' ? '0%' : '100%'}</td></tr>)}</tbody>
          </table></div>
          <h2 className="mt-6 text-sm font-medium text-body">Try a different evidence scenario</h2>
          <p className="mt-1 text-xs text-faint">These example controls explain the calculation. Results on working pages come from saved evidence and cannot be changed by clicking a status.</p>
          <ul className="mt-5 space-y-2">
            {criteria.filter(c => c.weight > 0).map((c) => (
              <li key={c.name} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-surface px-4 py-2.5">
                <div>
                  <p className="text-xs font-semibold text-ink">{c.name} <span className="font-normal text-faint">· weight {c.weight}</span></p>
                  <p className="text-[11px] text-faint">{c.note}</p>
                </div>
                <div className="flex items-center gap-1.5">
                  {(['matched', 'not-matched', 'unknown'] as const).map((s) => (
                    <button
                      key={s}
                      onClick={() => flip(c.name, s)}
                      aria-label={`${c.name}: ${RESULT_LABEL[s]}`}
                      aria-pressed={c.state === s}
                      className={`rounded-full px-2.5 py-1 text-[10px] font-semibold transition-all ${
                        c.state === s
                          ? s === 'matched'
                            ? 'bg-sage text-white'
                            : s === 'not-matched'
                              ? 'bg-amber-soft text-amberdeep'
                              : 'bg-faint text-white'
                          : 'border border-line text-faint hover:text-body'
                      }`}
                    >
                      {RESULT_LABEL[s]}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              onClick={() => setCriteria((prev) => prev.map((c) => c.state === 'unknown' ? { ...c, state: 'not-matched' as const, note: 'Example: missing information retrieved and checked; no match found' } : c))}
              className="rounded-full border border-line px-3.5 py-1.5 text-xs font-medium text-body transition-colors hover:border-accent hover:text-accent"
            >
              Simulate: missing information checked, no match
            </button>
            <button
              onClick={() => setCriteria([])}
              className="rounded-full border border-line px-3.5 py-1.5 text-xs font-medium text-body transition-colors hover:border-accent hover:text-accent"
            >
              Clear all criteria
            </button>
            <button
              onClick={() => setCriteria(RESULT_SIGNALS)}
              className="rounded-full border border-line px-3.5 py-1.5 text-xs font-medium text-body transition-colors hover:border-accent hover:text-accent"
            >
              Reset example
            </button>
          </div>
        </div>
      </Figure>

      <Callout>The range gives bounds for unresolved evidence, not a statistical confidence interval. Confidence measures weighted evidence availability, not likely job success. In the connected dashboard, weights apply to signal groups such as required skills and experience. This teaching calculator uses binary signal results to explain the arithmetic; actual signals can be partial, and negative-keyword or contradiction penalties can reduce scores. Exclusions are not a guarantee that a person disappears from search results.</Callout>
      <div className="grid gap-4 sm:grid-cols-2">
        <Callout>
          <strong>“No exact match” and “Not checked” differ.</strong> No exact match means saved information was checked and the term (or its aliases) wasn’t there. Not checked means the information was never retrieved or couldn’t be interpreted — an invitation to investigate, not evidence of absence.
        </Callout>
        <Callout tone="warn">
          <strong>Mentions and verification are different:</strong> credential text can contribute to a match, but does not verify validity or expiry. Search and routing hints have zero scoring weight.
        </Callout>
      </div>
    </ChapterShell>
  );
}
