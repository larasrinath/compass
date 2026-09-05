import { useEffect } from 'react'
import { CHAPTERS } from './content'
import { LearnHome, LearnChapter } from './LearnHome'
import { LearnNavigationContext, type LearnDestination } from './navigation'
import './learn.css'

export default function LearnPage({ chapter, onNavigate }: {
  chapter: string | null
  onNavigate: (next: LearnDestination) => void
}) {
  const current = CHAPTERS.find((item) => item.id === chapter)
  useEffect(() => {
    document.title = `${current?.title ?? 'How it works'} · Compass`
    window.scrollTo({ top: 0 })
  }, [chapter, current?.title])
  return (
    <LearnNavigationContext.Provider value={onNavigate}>
      <div id="compass-learn">
        {chapter && !current ? <section className="learn-page p-5">
          <h1>Chapter not found</h1>
          <p>This guide link does not match a chapter.</p>
          <button onClick={() => onNavigate({ name: 'learn' })}>All chapters</button>
        </section> : chapter ? <LearnChapter key={chapter} chapterId={chapter} /> : <LearnHome />}
      </div>
    </LearnNavigationContext.Provider>
  )
}
