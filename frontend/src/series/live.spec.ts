import { describe, expect, it } from 'vitest'

import type { SeriesOut } from '@/api/types'
import { t } from '@/i18n'

import { liveNote, waitingReasons } from './live'

function series(status: SeriesOut['status'], statuses: string[]): SeriesOut {
  return {
    status,
    episodes: statuses.map((s, i) => ({
      index: i + 1,
      title: `F${i + 1}`,
      role: '',
      target_minutes: 3,
      run_id: s === 'pending' ? null : `r${i + 1}`,
      status: s,
      total_cost_usd: 0,
      error: null,
    })),
  } as unknown as SeriesOut
}

describe('live notes', () => {
  it('speaks the console language for the script progress', () => {
    expect(liveNote('writing beat 4 of 7: Extremwetter')).toBe('Beat 4 von 7')
    expect(liveNote('reusing beat 2 of 6: x')).toBe('Beat 2 von 6 übernommen')
    expect(liveNote('target clamped to 4.2 minutes')).toBe('target clamped to 4.2 minutes')
  })

  it('names what each episode waits for', () => {
    expect(waitingReasons(series('planned', ['pending', 'pending']))).toEqual({
      1: t.series.waitingApproval,
      2: t.series.waitingApproval,
    })
    expect(waitingReasons(series('writing', ['completed', 'running', 'outlined']))).toEqual({
      3: 'wartet auf Folge 2',
    })
    expect(waitingReasons(series('outlining', ['outlined', 'running', 'queued']))).toEqual({
      1: t.series.waitingOutlines,
    })
  })
})
