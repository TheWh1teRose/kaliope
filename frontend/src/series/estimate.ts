/**
 * The start form's live estimate: how many episodes the document carries at a
 * given length, and what a chosen count means for length and coverage. The same
 * arithmetic as the planner (`backend/app/pipeline/nodes/series_plan.py`), so
 * the form and the plan agree.
 */

export const MIN_EPISODES = 2
export const MAX_EPISODES = 8
/** Below this many minutes per episode a series is refused. */
export const MIN_VIABLE_MINUTES = 3

export type EstimateTone = 'ok' | 'warn' | 'fail'

export interface SeriesEstimate {
  /** The count the planner will use. */
  episodes: number
  /** What the budget suggests at this length. */
  suggested: number
  /** Requested minutes, or the even share when the document is too thin for three minutes each. */
  perEpisode: number
  /** Minutes of the document the series uses. */
  used: number
  tone: EstimateTone
  reason: 'fits' | 'tooFew' | 'tooThin' | 'more' | 'fewer'
  /** Rough cost range in dollars. */
  low: number
  high: number
}

export function suggestedCount(supportable: number, minutes: number): number {
  return Math.max(MIN_EPISODES, Math.min(MAX_EPISODES, Math.floor(supportable / Math.max(minutes, 1))))
}

export function clampCount(value: number): number {
  return Math.max(MIN_EPISODES, Math.min(MAX_EPISODES, Math.round(value)))
}

/**
 * Rough cost of a series: per episode about $0.20–0.35 for selection and
 * outline plus $0.40–0.75 per 15 minutes of script, and $0.15 for the plan.
 */
export function costRange(episodes: number, perEpisode: number): [number, number] {
  const scale = perEpisode / 15
  return [episodes * (0.2 + 0.4 * scale) + 0.15, episodes * (0.35 + 0.75 * scale) + 0.15]
}

export function estimateSeries(
  supportable: number,
  minutes: number,
  chosen: number | null,
): SeriesEstimate {
  const floor = Math.floor(supportable / Math.max(minutes, 1))
  const suggested = suggestedCount(supportable, minutes)
  const episodes = chosen === null ? suggested : clampCount(chosen)
  let perEpisode = minutes
  let tone: EstimateTone = 'ok'
  let reason: SeriesEstimate['reason'] = 'fits'

  if (supportable / episodes < MIN_VIABLE_MINUTES) {
    tone = 'fail'
    reason = 'tooThin'
    perEpisode = supportable / episodes
  } else if (episodes * minutes > supportable) {
    tone = 'warn'
    reason = chosen === null && floor < MIN_EPISODES ? 'tooFew' : 'more'
  } else if (chosen !== null && episodes < suggested) {
    reason = 'fewer'
  }
  const used = Math.min(supportable, episodes * perEpisode)
  const [low, high] = costRange(episodes, perEpisode)
  return { episodes, suggested, perEpisode, used, tone, reason, low, high }
}
