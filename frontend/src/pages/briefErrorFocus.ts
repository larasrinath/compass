interface FocusTarget {
  focus(): void
}

interface QueryRoot {
  querySelector<T extends FocusTarget>(selector: string): T | null
}

/** Focus the first server-rejected brief field, or the summary as fallback. */
export function focusBriefError(
  field: string | undefined,
  fallback: FocusTarget | null,
  root: QueryRoot = document,
): void {
  const input = field
    ? root.querySelector<HTMLInputElement | HTMLTextAreaElement>(
        `[data-field-prefix="${field}"] input, ` +
          `[data-field-prefix="${field}"] textarea`,
      )
    : null
  ;(input ?? fallback)?.focus()
}
