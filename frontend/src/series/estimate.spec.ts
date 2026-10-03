import { describe, expect, it } from 'vitest'

import { estimateSeries, suggestedCount } from './estimate'

describe('series estimate', () => {
  it('suggests what the budget carries, between two and eight episodes', () => {
    expect(suggestedCount(46.2, 15)).toBe(3)
    expect(suggestedCount(20, 15)).toBe(2)
    expect(suggestedCount(500, 5)).toBe(8)
  })

  it('follows the suggestion until the reviewer sets a count', () => {
    const auto = estimateSeries(46.2, 15, null)
    expect(auto).toMatchObject({ episodes: 3, suggested: 3, perEpisode: 15, reason: 'fits', tone: 'ok' })
    expect(auto.used).toBe(45)
  })

  it('keeps the length and spreads the source when more episodes are asked for', () => {
    const more = estimateSeries(46.2, 15, 5)
    expect(more).toMatchObject({ episodes: 5, perEpisode: 15, reason: 'more', tone: 'warn' })
  })

  it('says that fewer episodes leave material out', () => {
    const fewer = estimateSeries(46.2, 15, 2)
    expect(fewer).toMatchObject({ episodes: 2, perEpisode: 15, reason: 'fewer', tone: 'ok' })
    expect(fewer.used).toBe(30)
  })

  it('refuses a count that leaves under three minutes per episode', () => {
    expect(estimateSeries(10, 15, 4)).toMatchObject({
      episodes: 4,
      perEpisode: 2.5,
      reason: 'tooThin',
      tone: 'fail',
    })
    expect(estimateSeries(10, 5, 8)).toMatchObject({
      episodes: 8,
      perEpisode: 1.25,
      reason: 'tooThin',
      tone: 'fail',
    })
    expect(estimateSeries(11.85, 15, 4).perEpisode).toBe(11.85 / 4)
  })

  it('warns when the suggestion is a series of one', () => {
    expect(estimateSeries(20, 15, null)).toMatchObject({ episodes: 2, reason: 'tooFew', tone: 'warn' })
  })
})
