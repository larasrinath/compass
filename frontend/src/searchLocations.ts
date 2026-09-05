import { runSearch, type SearchInput } from './api/client'
import { splitLocations } from './locations'

export async function searchLocations(input: SearchInput) {
  const locations = splitLocations(input.location)
  const queued: Awaited<ReturnType<typeof runSearch>>[] = []
  const failed: string[] = []
  for (const location of locations.length ? locations : [null]) {
    try {
      queued.push(await runSearch({ ...input, location }))
    } catch (error) {
      // Stop on a queue/connection failure; do not retry already queued locations.
      if (!queued.length) throw error
      failed.push(...locations.slice(queued.length))
      break
    }
  }
  return { queued, failed, multiple: locations.length > 1 }
}
