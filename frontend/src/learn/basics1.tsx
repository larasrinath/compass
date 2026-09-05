import { useEffect, useMemo, useState } from 'react';
import { Check, Download, Plus, Search, UserRound, X } from 'lucide-react';
import { ChapterShell, Figure, Callout, DownArrow, FictionalTag, StatePill } from './ui';
import { FICTION } from './content';

/* ================= Chapter 1 — What Compass does ================= */

const STAGES = [
  {
    label: 'Define the role', where: 'Search criteria',
    detail: 'Start with a description, then enter the criteria yourself. Add skills, credentials and nice-to-haves, choose one or more locations, and set minimum experience in years. Target titles, industries, network and company preferences stay in the optional section.',
    outcome: 'Continue to search saves the brief. Every search uses those saved criteria.',
  },
  {
    label: 'Find candidates', where: 'Find candidates',
    detail: 'Run a search from the brief. Each location queues a separate request. Results enter one candidate pool; repeated profiles are combined while keeping a link to every search that found them.',
    outcome: 'Each new person is queued for a profile and experience download. Scores update as saved evidence arrives.',
  },
  {
    label: 'Download and score', where: 'Automatic downloads, then Review',
    detail: 'By default, Compass queues up to 1,000 new profile downloads per search batch. Settings lets you lower the batch limit and choose one or two simultaneous profile downloads, within the connector’s capacity. Existing downloads are reused. It saves each original response before extracting details and calculating a score. Review the saved text, career history and available sections; request additional sections when something useful is missing.',
    outcome: 'Saved profile evidence stays on this computer and remains available without the connector.',
  },
  {
    label: 'Review and compare', where: 'Compare matches',
    detail: 'Check the candidate list and record your review note to unlock ranking. Open a candidate to see the score, uncertainty range and signal breakdown. Select two or three people, then choose View comparison to read their evidence side by side.',
    outcome: 'Adjust scoring weights in Settings when priorities change. Compass recalculates from saved evidence without another download.',
  },
  {
    label: 'Make your own assessment', where: 'Your review',
    detail: 'Open the source behind a claim and confirm only what you have actually checked. A text match is not independent verification; an unchecked criterion means more evidence may be needed. The score supports your review, and the decision remains yours.',
    outcome: 'Return through Saved searches. Each run previews three saved profiles; Open results brings back its full candidate pool.',
  },
];

export function Ch1() {
  return (
    <ChapterShell
      chapterId="what-compass-does"
      kicker="The basics · 1 of 9"
      title="What Compass does"
      intro="Start Compass with ./compass from the repository folder. The app and first-time LinkedIn sign-in window open for you. Finish signing in, then create your search criteria and choose Run search. Compass is your local candidate research workspace. Describe a role, search LinkedIn, automatically download profiles, and compare the evidence against your criteria. You choose what matters and inspect the sources behind the results."
    >
      <section>
        <h2 className="font-display text-lg font-bold text-ink">From search criteria to your decision</h2>
        <p className="mt-2 text-sm leading-relaxed text-faint">Follow the work from top to bottom. Each stage explains what you do, what Compass does, and what you can review next.</p>
        <div className="mt-5">
          <Figure caption="New searches and profile requests use the separate LinkedIn connector. Saved evidence, local scoring and comparison remain available while the connector is offline.">
            <ol className="learn-flow" aria-label="Compass workflow">
              {STAGES.map((stage, index) => <li key={stage.label}>
                <div className="learn-flow-card">
                  <span className="learn-flow-number" aria-hidden="true">{index + 1}</span>
                  <div className="learn-flow-content">
                    <p className="learn-flow-location">{stage.where}</p>
                    <h3>{stage.label}</h3>
                    <p>{stage.detail}</p>
                    <p className="learn-flow-outcome">{stage.outcome}</p>
                  </div>
                </div>
                {index < STAGES.length - 1 && <DownArrow />}
              </li>)}
            </ol>
          </Figure>
        </div>
      </section>
    </ChapterShell>
  );
}

/* ================= Chapter 2 — Set up your role ================= */

function canon(term: string): string {
  return term.trim().normalize('NFKC').toLowerCase();
}

function ChipInput({ values, onChange, placeholder }: { values: string[]; onChange: (v: string[]) => void; placeholder: string }) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const v = draft.trim();
    if (v && !values.some((x) => x.toLowerCase() === v.toLowerCase())) onChange([...values, v]);
    setDraft('');
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      {values.map((v) => (
        <span key={v} className="chip text-xs">
          {v}
          <button onClick={() => onChange(values.filter((x) => x !== v))} aria-label={`Remove ${v}`} className="text-faint hover:text-rust">
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <span className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-line px-3 py-1.5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
          aria-label={placeholder}
          placeholder={placeholder}
          className="w-32 bg-transparent text-xs text-ink placeholder:text-faint/70 focus:outline-none"
        />
        <button onClick={add} aria-label="Add" className="text-accent"><Plus className="h-3.5 w-3.5" /></button>
      </span>
    </div>
  );
}

export function Ch2() {
  const [description, setDescription] = useState(`${FICTION.role.charAt(0).toUpperCase() + FICTION.role.slice(1)}, ideally with payments experience. Berlin or remote in Europe.`);
  const [required, setRequired] = useState<string[]>(['Go', 'PostgreSQL']);
  const [optional, setOptional] = useState<string[]>(['Kubernetes']);
  const [include, setInclude] = useState<string[]>(['payments']);
  const [exclude, setExclude] = useState<string[]>(['recruiter']);
  const [saved, setSaved] = useState(false);
  const [locations, setLocations] = useState(['Berlin']);
  const [years, setYears] = useState(5);

  const conflicts = useMemo(() => {
    const inc = new Map(include.map((t) => [canon(t), t]));
    return exclude.filter((t) => inc.has(canon(t))).map((t) => ({ excluded: t, included: inc.get(canon(t))! }));
  }, [include, exclude]);

  return (
    <ChapterShell
      chapterId="set-up-role"
      kicker="The basics · 2 of 9"
      title="Set up your role"
      intro="The Search criteria page keeps your description and search criteria on one page. Enter the description, add the criteria you want to check, then select “Continue to search”. The description does not automatically fill in the criteria. Searches use your latest saved brief."
    >
      <Figure caption="Edit the description and criteria together, then save once. This example starts with manually entered criteria. Skills and nice-to-haves appear together; locations support multiple entries. Target titles and industries are in Optional preferences. Existing aliases are retained in saved data but are not edited on this screen. Try adding “payments” to Exclusions to see the conflict check.">
        <div className="fade-enter space-y-5">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-ink">Search criteria</p>
              <FictionalTag />
            </div>
            <label className="block text-sm font-semibold text-ink">
              Role description
              <textarea className="mt-2 w-full rounded-xl border border-line bg-canvas p-3 font-normal" rows={8} value={description} onChange={event => { setDescription(event.target.value); setSaved(false); }} />
            </label>
            <div>
              <p className="text-sm font-semibold text-ink">Skills & keywords</p>
              <div className="mt-2"><ChipInput values={required} onChange={(v) => { setRequired(v); setSaved(false); }} placeholder="Add required skill" /></div>
            </div>
            <div>
              <p className="text-sm font-semibold text-ink">Nice-to-haves</p>
              <div className="mt-2"><ChipInput values={optional} onChange={(v) => { setOptional(v); setSaved(false); }} placeholder="Add a nice-to-have" /></div>
            </div>
            <div><p className="text-sm font-medium text-ink">Locations</p><div className="mt-2"><ChipInput values={locations} onChange={v => { setLocations(v); setSaved(false); }} placeholder="Add a location" /></div></div>
            <div><p className="text-sm font-medium text-ink">Minimum experience</p><div className="mt-2 inline-flex h-10 items-center gap-4 rounded-full border border-line px-3"><button aria-label="Decrease minimum experience" disabled={years === 0} onClick={() => { setYears(v => Math.max(0,v-1)); setSaved(false); }}>−</button><span className="text-sm">{years ? `${years}+ years` : 'Any'}</span><button aria-label="Increase minimum experience" onClick={() => { setYears(v => v+1); setSaved(false); }}>+</button></div></div>
            <h2 className="border-t border-line pt-4 text-sm font-medium text-faint">Optional preferences</h2>
            <p className="text-xs text-faint">Target titles, industries, network distance, and company controls appear here. Positive keywords and Exclusions follow the company fields.</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <p className="text-sm font-semibold text-ink">Positive keywords</p>
                <div className="mt-2"><ChipInput values={include} onChange={(v) => { setInclude(v); setSaved(false); }} placeholder="e.g. payments" /></div>
              </div>
              <div>
                <p className="text-sm font-semibold text-ink">Exclusions</p>
                <div className="mt-2"><ChipInput values={exclude} onChange={(v) => { setExclude(v); setSaved(false); }} placeholder="e.g. recruiter" /></div>
              </div>
            </div>

            {conflicts.length > 0 && (
              <div className="rounded-xl border border-rust/30 bg-rust-soft px-4 py-3 text-sm text-body">
                <p className="font-semibold text-rust">Fix this conflict before saving</p>
                {conflicts.map((c) => (
                  <p key={c.excluded} className="mt-1 text-xs leading-relaxed">
                    “{c.included}” is both included and excluded. Remove it from one side.
                  </p>
                ))}
              </div>
            )}

            {saved && (
              <div className="flex items-center gap-2 rounded-xl border border-sage/30 bg-sage-soft px-4 py-3 text-sm font-medium text-sage">
                <Check className="h-4 w-4" /> Example saved — no real brief was changed.
              </div>
            )}

            <div className="flex items-center justify-between border-t border-line pt-4">
              <p className="text-xs text-faint">Credentials are added in Skills & keywords using the filter type.</p>
              <button
                onClick={() => setSaved(true)}
                disabled={conflicts.length > 0 || !description.trim()}
                className="inline-flex shrink-0 items-center gap-2 rounded-full bg-ink px-5 py-2.5 text-sm font-semibold text-canvas hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-40"
              >
                Continue to search
              </button>
            </div>
          </div>
      </Figure>

      <Callout tone="warn">
        <strong>Accuracy note:</strong> nothing in the description is auto-extracted into the form. You fill in each criterion, and conflicting include/exclude keywords must be resolved before the brief can be saved.
      </Callout>
    </ChapterShell>
  );
}

/* ================= Chapter 3 — Discover candidates ================= */

interface PoolEntry {
  name: string;
  foundIn: string[];
  downloaded: boolean;
}

const SEARCH_A = ['Robin Serrano', 'Marta Voss', 'Kofi Annan-Lee'];
const SEARCH_B = ['Robin Serrano', 'Elif Demir', 'Jonas Reyes'];

const SearchCard = ({ which, names, added, addResults }: { which: 'A' | 'B'; names: string[]; added: boolean; addResults: (which: 'A' | 'B') => void }) => (
    <div className="rounded-xl border border-line bg-canvas p-4">
      <p className="text-xs font-semibold text-ink">Search {which}: {FICTION.searches[which === 'A' ? 0 : 1]}</p>
      <ul className="mt-2 space-y-1.5">
        {names.map((n) => (
          <li key={n} className={`flex items-center gap-2 text-sm ${n === FICTION.candidate ? 'font-semibold text-accent' : 'text-body'}`}>
            <UserRound className="h-3.5 w-3.5 text-faint" /> {n}
            {n === FICTION.candidate && <span className="text-[10px] font-medium text-faint">appears in both searches</span>}
          </li>
        ))}
      </ul>
      <button
        onClick={() => addResults(which)}
        disabled={added}
        className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs font-medium text-body transition-colors hover:border-accent hover:text-accent disabled:opacity-40"
      >
        {added ? <Check className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
        {added ? 'Added to pool' : 'Add results to pool'}
      </button>
    </div>
  );

export function Ch3() {
  const [pool, setPool] = useState<PoolEntry[]>([]);
  const [addedA, setAddedA] = useState(false);
  const [addedB, setAddedB] = useState(false);
  const [filter, setFilter] = useState<'all' | 'A' | 'B'>('all');
  const [nameQuery, setNameQuery] = useState('');

  useEffect(() => {
    const next = pool.find(person => !person.downloaded);
    if (!next) return;
    const timer = window.setTimeout(() => setPool(previous => previous.map(person => person.name === next.name ? { ...person, downloaded: true } : person)), 700);
    return () => window.clearTimeout(timer);
  }, [pool]);

  const addResults = (which: 'A' | 'B') => {
    const names = which === 'A' ? SEARCH_A : SEARCH_B;
    const label = which === 'A' ? FICTION.searches[0] : FICTION.searches[1];
    setPool((prev) => {
      const next = prev.map((p) => ({ ...p, foundIn: [...p.foundIn] }));
      names.forEach((n) => {
        const existing = next.find((p) => p.name === n);
        if (existing) {
          if (!existing.foundIn.includes(label)) existing.foundIn.push(label);
        } else {
          next.push({ name: n, foundIn: [label], downloaded: false });
        }
      });
      return next;
    });
    if (which === 'A') setAddedA(true); else setAddedB(true);
  };

  const visible = pool.filter(
    (p) =>
      (filter === 'all' || p.foundIn.some((f) => f === FICTION.searches[filter === 'A' ? 0 : 1])) &&
      p.name.toLowerCase().includes(nameQuery.toLowerCase()),
  );



  return (
    <ChapterShell
      chapterId="discover-candidates"
      kicker="The basics · 3 of 9"
      title="Find, download and score candidates"
      intro="Searching LinkedIn uses your keywords and available preferences — location, network distance, a current-company identifier. Results enter a saved candidate pool: when the same person appears twice, Compass combines them into one record that keeps a link to every search that found them. With automatic downloads enabled in Settings, newly found people are queued for a profile and experience download. Scores update as each download completes. Choose 3rd-degree and beyond or all networks to look beyond your direct and mutual connections."
    >
      <Figure caption="Two fictional searches both return Robin Serrano. In the pool she becomes one candidate record with two search links — not a duplicate. Add both searches to see the merge and the simulated automatic queue. With the default Settings, Compass follows additional results pages with the same filters, downloading up to two profiles at a time in separate browser tabs. Each search batch queues up to 1,000 new profile downloads. Existing downloads do not use that allowance, and saved candidates can keep growing across searches. Stop discovery ends further pages while queued downloads finish.">
        <div className="grid gap-4 sm:grid-cols-2">
          <SearchCard which="A" names={SEARCH_A} added={addedA} addResults={addResults} />
          <SearchCard which="B" names={SEARCH_B} added={addedB} addResults={addResults} />
        </div>

        <div className="mt-5 rounded-xl border border-accent/30 bg-accent-soft/40 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs font-bold uppercase tracking-wide text-accent">Saved candidate pool</p>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={filter}
                onChange={(e) => setFilter(e.target.value as 'all' | 'A' | 'B')}
                className="rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-body focus:outline-none"
                aria-label="Filter pool by saved search"
              >
                <option value="all">All searches</option>
                <option value="A">Search A only</option>
                <option value="B">Search B only</option>
              </select>
              <input
                value={nameQuery}
                onChange={(e) => setNameQuery(e.target.value)}
                aria-label="Find a saved candidate by name"
                placeholder="Find by name"
                className="w-32 rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-body placeholder:text-faint/70 focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          {pool.length === 0 ? (
            <p className="mt-3 text-sm text-faint">The pool is empty. Add results from either search above.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {visible.map((p) => (
                <li key={p.name} className="rounded-lg border border-line bg-surface p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-ink">{p.name} <FictionalTag /></p>
                    {p.downloaded ? (
                      <StatePill tone="sage"><span className="inline-flex items-center gap-1"><Download className="h-3 w-3" /> Profile information saved</span></StatePill>
                    ) : (
                      <StatePill tone="plain"><span className="inline-flex items-center gap-1"><Search className="h-3 w-3" /> Waiting to download</span></StatePill>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] text-faint">Found in: {p.foundIn.join(' · ')}</p>

                </li>
              ))}
              {visible.length === 0 && <p className="mt-3 text-sm text-faint">Nobody matches that filter.</p>}
            </ul>
          )}
        </div>
      </Figure>

      <Callout>
        Location is a search preference, not a guaranteed geographic filter. Verify it in saved profile text. Searches are recorded automatically under <strong>Saved searches</strong> and can be reopened anytime — each search filters the shared session pool through its source links. One candidate can belong to several searches. After you confirm the candidate-list review, Find candidates also offers a Ranked list beside Cards: highest scores first, with rank, score and confidence in each row. Unscored profiles appear last. Both views use the same search and name filters.
      </Callout>
    </ChapterShell>
  );
}
