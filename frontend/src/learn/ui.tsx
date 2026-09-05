import React, { useEffect, useRef } from 'react';
import { ArrowLeft, ArrowRight, BookOpen } from 'lucide-react';
import { CHAPTERS } from './content';
import { useLearnNavigation } from './navigation';

/* ---------- Chapter shell ---------- */

export function ChapterShell({
  chapterId,
  kicker,
  title,
  intro,
  children,
}: {
  chapterId: string;
  kicker: string;
  title: string;
  intro: string;
  children: React.ReactNode;
}) {
  const { navigate } = useLearnNavigation();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [chapterId]);
  const idx = CHAPTERS.findIndex((c) => c.id === chapterId);
  const prev = idx > 0 ? CHAPTERS[idx - 1] : null;
  const next = idx < CHAPTERS.length - 1 ? CHAPTERS[idx + 1] : null;

  return (
    <div className="learn-page mx-auto w-full min-w-0 max-w-3xl px-5 pb-24 pt-10">
      <button
        onClick={() => navigate({ name: 'learn' })}
        className="mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-faint transition-colors hover:text-body"
      >
        <BookOpen className="h-4 w-4" /> All chapters
      </button>
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">{kicker}</p>
      <h1 ref={heading} tabIndex={-1} className="mt-3 font-display text-2xl font-extrabold tracking-tight text-ink sm:text-3xl">{title}</h1>
      <p className="mt-3 text-base leading-relaxed text-body">{intro}</p>
      <p className="mt-4 text-xs leading-relaxed text-faint">Interactive teaching examples only. Controls here do not search LinkedIn, download profiles, or change your saved workspace. Examples reset when you leave a chapter.</p>
      <div className="mt-8 space-y-10">{children}</div>

      <div className="mt-14 flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center border-t border-line pt-6">
        {prev ? (
          <button
            onClick={() => navigate({ name: 'learn', chapter: prev.id })}
            className="inline-flex items-center gap-2 text-sm font-medium text-faint transition-colors hover:text-body"
          >
            <ArrowLeft className="h-4 w-4" /> {prev.title}
          </button>
        ) : (
          <span />
        )}
        {next ? (
          <button
            onClick={() => navigate({ name: 'learn', chapter: next.id })}
            className="ml-auto inline-flex items-center gap-2 rounded-full bg-ink px-5 py-2.5 text-sm font-semibold text-canvas transition-colors hover:bg-accent-strong"
          >
            Next: {next.title} <ArrowRight className="h-4 w-4" />
          </button>
        ) : (
          <button
            onClick={() => navigate({ name: 'home' })}
            className="ml-auto inline-flex items-center gap-2 rounded-full bg-ink px-5 py-2.5 text-sm font-semibold text-canvas transition-colors hover:bg-accent-strong"
          >
            Start a search <ArrowRight className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

/* ---------- Figure with accessible caption ---------- */

export function Figure({ caption, children }: { caption: string; children: React.ReactNode }) {
  return (
    <figure className="rounded-2xl border border-line bg-surface p-5 sm:p-6">
      {children}
      <figcaption className="mt-4 border-t border-line pt-3 text-xs leading-relaxed text-faint">{caption}</figcaption>
    </figure>
  );
}

/* ---------- Callout ---------- */

export function Callout({ tone = 'info', children }: { tone?: 'info' | 'warn'; children: React.ReactNode }) {
  return (
    <div
      className={`rounded-2xl px-5 py-4 text-sm leading-relaxed ${
        tone === 'warn' ? 'border border-amberdeep/25 bg-amber-soft text-body' : 'bg-accent-soft/70 text-body'
      }`}
    >
      {children}
    </div>
  );
}

/* ---------- Small diagram primitives ---------- */

export function Box({
  label,
  sub,
  tone = 'plain',
  className = '',
}: {
  label: string;
  sub?: string;
  tone?: 'plain' | 'accent' | 'sage' | 'dashed';
  className?: string;
}) {
  const tones = {
    plain: 'border-line bg-canvas text-ink',
    accent: 'border-accent/40 bg-accent-soft text-accent-strong',
    sage: 'border-sage/40 bg-sage-soft text-sage',
    dashed: 'border-dashed border-faint/60 bg-transparent text-faint',
  };
  return (
    <div className={`rounded-xl border px-3 py-2.5 text-center ${tones[tone]} ${className}`}>
      <p className="text-xs font-semibold leading-tight">{label}</p>
      {sub && <p className="mt-0.5 text-[11px] leading-tight opacity-80">{sub}</p>}
    </div>
  );
}

export function DownArrow({ label }: { label?: string }) {
  return <div className="learn-flow-connector">
    <svg aria-hidden="true" width="18" height="30" viewBox="0 0 18 30" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 2v25m-5-5 5 5 5-5" />
    </svg>
    {label && <span>{label}</span>}
  </div>;
}

export function FictionalTag() {
  return (
    <span className="fictional-tag inline-flex shrink-0 items-center whitespace-nowrap rounded-full border border-dashed border-faint/60 px-2 py-0.5 font-sans text-[10px] font-semibold leading-5 uppercase tracking-wide text-faint">
      Fictional example
    </span>
  );
}

export function StatePill({ tone, children }: { tone: 'sage' | 'amber' | 'rust' | 'plain'; children: React.ReactNode }) {
  const tones = {
    sage: 'bg-sage-soft text-sage',
    amber: 'bg-amber-soft text-amberdeep',
    rust: 'bg-rust-soft text-rust',
    plain: 'border border-dashed border-faint/60 text-faint',
  };
  return <span className={`inline-flex shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${tones[tone]}`}>{children}</span>;
}
