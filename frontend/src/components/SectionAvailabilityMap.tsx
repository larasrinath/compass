const SECTIONS = [
  'main_profile',
  'experience',
  'education',
  'certifications',
  'skills',
  'projects',
] as const

export function SectionAvailabilityMap({
  available,
}: {
  available: Record<string, { char_len: number; field_count: number }>
}) {
  return (
    <ul aria-label="Profile section availability" className="section-map">
      {SECTIONS.map((name) => {
        const section = available[name]
        return (
          <li className={section ? 'available' : 'missing'} key={name}>
            <span aria-hidden="true">{section ? '●' : '○'}</span>
            <strong>{name.replaceAll('_', ' ')}</strong>
            <small>
              {section
                ? `${section.field_count} parsed fields · ${section.char_len} characters`
                : 'Not retrieved'}
            </small>
          </li>
        )
      })}
    </ul>
  )
}
