import { ArrowRight, BookOpen } from 'lucide-react';
import { CHAPTERS } from './content';
import { useLearnNavigation } from './navigation';
import { Ch1, Ch2, Ch3 } from './basics1';
import { Ch4, Ch5 } from './basics2';
import { Ch6, Ch7 } from './results1';
import { Ch8, Ch9 } from './results2';
import { TourReview, TourCompare } from './tours';

const GROUPS = ['The basics', 'Working with results', 'Guided tours'];

export function LearnHome() {
  const { navigate } = useLearnNavigation();
  return (
    <div className="learn-page mx-auto w-full min-w-0 max-w-3xl px-5 pb-24 pt-10">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Learn</p>
      <h1 className="mt-3 font-display text-2xl font-extrabold tracking-tight text-ink sm:text-3xl">How Compass works</h1>
      <p className="mt-3 max-w-xl text-base leading-relaxed text-body">
        Short, interactive chapters that teach you how to use Compass, what happens behind each action, and how to read
        the results. Everything shown is a fictional, illustrative example — no real candidate data.
      </p>

      {GROUPS.map((group) => (
        <section key={group} className="mt-10">
          <h2 className="text-xs font-bold uppercase tracking-widest text-faint">{group}</h2>
          <div className="mt-3 divide-y divide-line rounded-2xl border border-line bg-surface">
            {CHAPTERS.filter((c) => c.group === group).map((c) => {
              const num = CHAPTERS.indexOf(c) + 1;
              return (
                <button
                  key={c.id}
                  onClick={() => navigate({ name: 'learn', chapter: c.id })}
                  className="group flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-subtle/60"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-subtle text-xs font-bold text-faint transition-colors group-hover:bg-accent-soft group-hover:text-accent">
                    {c.group === 'Guided tours' ? <BookOpen className="h-3.5 w-3.5" /> : num}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-ink">{c.title}</span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-faint">{c.blurb}</span>
                  </span>
                  <ArrowRight className="h-4 w-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

export function LearnChapter({ chapterId }: { chapterId: string }) {
  switch (chapterId) {
    case 'what-compass-does': return <Ch1 />;
    case 'set-up-role': return <Ch2 />;
    case 'discover-candidates': return <Ch3 />;
    case 'download-evidence': return <Ch4 />;
    case 'after-a-request': return <Ch5 />;
    case 'review-and-compare': return <Ch6 />;
    case 'scores-uncertainty': return <Ch7 />;
    case 'priorities-verify': return <Ch8 />;
    case 'return-to-work': return <Ch9 />;
    case 'tour-review': return <TourReview />;
    case 'tour-compare': return <TourCompare />;
    default: return <LearnHome />;
  }
}
