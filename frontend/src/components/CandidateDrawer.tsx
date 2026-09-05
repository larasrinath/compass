import { useEffect, useRef, type ReactNode } from 'react'
import { CompassIcon } from './CompassIcon'

export function CandidateDrawer({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = dialogRef.current!
    const previousFocus = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    dialog.showModal()
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.close()
      document.body.style.overflow = previousOverflow
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
    }
  }, [])
  return (
    <dialog ref={dialogRef} className="candidate-drawer" aria-label="Candidate review"
      onCancel={event => { event.preventDefault(); onClose() }}
      onClick={event => {
        if (event.target !== event.currentTarget) return
        const rect = event.currentTarget.getBoundingClientRect()
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose()
      }}>
      <button type="button" className="profile-close" aria-label="Close candidate profile" onClick={onClose}><CompassIcon name="close" /></button>
      {children}
    </dialog>
  )
}
