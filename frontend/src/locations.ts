// Semicolons separate alternatives; commas stay inside names such as Austin, TX.
export function splitLocations(value?: string | null): string[] {
  return [...new Set((value ?? '').split(';').map(item => item.trim()).filter(Boolean))]
}
