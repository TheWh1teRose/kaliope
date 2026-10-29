import { describe, expect, it } from 'vitest'

import {
  choose,
  clock,
  emptySetup,
  estimate,
  firstMinuteIds,
  missingVoices,
  normalise,
  payload,
  pieces,
  rangeIds,
  type Line,
} from './synthesis'

const lines: Line[] = [
  { segment_id: 'a', speaker: 'Host', tagged: '[warm] Hallo zusammen.' },
  { segment_id: 'b', speaker: 'Gast', tagged: 'x'.repeat(990) },
  { segment_id: 'c', speaker: 'Host', tagged: 'Und tschüss.' },
]

describe('synthesis setup', () => {
  it('stores a full selection as null and keeps script order', () => {
    expect(choose(lines, ['c', 'a', 'b'])).toBeNull()
    expect(choose(lines, ['c', 'a'])).toEqual(['a', 'c'])
    expect(choose(lines, [])).toEqual([])
    expect(rangeIds(lines, 3, 2)).toEqual(['b', 'c'])
    expect(rangeIds(lines, 0, 99)).toEqual(['a', 'b', 'c'])
  })

  it('picks whole segments for about a minute, counting edits', () => {
    const setup = emptySetup()
    expect(firstMinuteIds(lines, setup)).toEqual(['a', 'b'])
    setup.edits = { a: 'x'.repeat(1200) }
    expect(firstMinuteIds(lines, setup)).toEqual(['a'])
  })

  it('estimates characters, cost and spoken length of the selection with edits', () => {
    const setup = { ...emptySetup(), segment_ids: ['a', 'c'], edits: { c: '[sighs] Ciao.' } }
    const result = estimate(lines, setup, 0.08, 10)
    expect(result.segments).toBe(2)
    expect(result.characters).toBe('[warm] Hallo zusammen.'.length + '[sighs] Ciao.'.length)
    expect(result.costUsd).toBeCloseTo((result.characters / 1000) * 0.08)
    // Tags are not heard: "Hallo zusammen." + "Ciao." at ten characters a second.
    expect(result.seconds).toBeCloseTo(('Hallo zusammen.'.length + 'Ciao.'.length) / 10)
  })

  it('asks for voices only of chosen speakers and sends only real edits', () => {
    const setup = { ...emptySetup(), segment_ids: ['a'], edits: { a: lines[0]!.tagged, c: 'Neu.' } }
    setup.voice_cast.voices = [{ speaker: 'Gast', voice_id: '' }]
    expect(missingVoices(lines, setup)).toEqual(['Host'])
    setup.voice_cast.voices = [{ speaker: 'host', voice_id: 'v1' }]
    expect(missingVoices(lines, setup)).toEqual([])
    expect(payload(lines, setup)).toEqual({ ...setup, segment_ids: ['a'], edits: { c: 'Neu.' } })
  })

  it('reads setups saved before selection and edits existed', () => {
    const old = normalise({ artifact_hash: 'h', voice_cast: { voices: [], model_id: 'eleven_v3', stability: 1 } })
    expect(old.segment_ids).toBeNull()
    expect(old.edits).toEqual({})
    expect(old.voice_cast.model_id).toBe('eleven_v3')
  })

  it('splits tags from spoken text and formats durations', () => {
    expect(pieces('[warm] Hallo [pause] du')).toEqual([
      { text: '[warm]', tag: true },
      { text: ' Hallo ', tag: false },
      { text: '[pause]', tag: true },
      { text: ' du', tag: false },
    ])
    expect(clock(65.4)).toBe('1:05')
  })
})
