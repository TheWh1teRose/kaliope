/**
 * Turning a series' raw state and events into what the canvas says about a node:
 * what it is doing now, or what it waits for.
 */

import type { SeriesOut } from '@/api/types'
import { fill, t } from '@/i18n'

const WRITING = /^writing beat (\d+) of (\d+)/
const REUSING = /^reusing beat (\d+) of (\d+)/

/** A node's progress line from the server, in the console's words where it has them. */
export function liveNote(message: string): string {
  const writing = WRITING.exec(message)
  if (writing) return fill(t.series.beatWriting, { k: writing[1], n: writing[2] })
  const reusing = REUSING.exec(message)
  if (reusing) return fill(t.series.beatReused, { k: reusing[1], n: reusing[2] })
  return message
}

const SCRIPT_DONE = new Set(['completed', 'in_review', 'reviewed'])

/**
 * Why each episode's next node is not running yet, keyed by episode index.
 * Episodes that are running, finished or failed have no entry.
 */
export function waitingReasons(series: SeriesOut): Record<number, string> {
  const reasons: Record<number, string> = {}
  const episodes = series.episodes
  for (const episode of episodes) {
    if (
      episode.status === 'running' ||
      episode.status === 'failed' ||
      episode.status === 'stopped' ||
      SCRIPT_DONE.has(episode.status)
    ) {
      continue
    }
    if (series.status === 'planned') {
      reasons[episode.index] = t.series.waitingApproval
    } else if (series.status === 'queued' || series.status === 'planning') {
      reasons[episode.index] = t.series.waitingPlan
    } else if (episode.status === 'outlined') {
      const before = episodes.find((other) => other.index === episode.index - 1)
      if (series.status === 'outlining') reasons[episode.index] = t.series.waitingOutlines
      else if (before && !SCRIPT_DONE.has(before.status)) {
        reasons[episode.index] = fill(t.series.waitingFor, { n: before.index })
      }
    }
  }
  return reasons
}
