import { describe, expect, it } from 'vitest'

import { FIELD_NAME, originsFor, placeholders } from './promptSetup'

describe('prompt template placeholders', () => {
  it('lists each placeholder once with whether its field is filled, empty or missing', () => {
    expect(
      placeholders('{{a}} {{ b }} {{a}} {{c}} {json}', { a: 'x', b: '  ', d: 'unused' }),
    ).toEqual([
      { name: 'a', state: 'ok' },
      { name: 'b', state: 'empty' },
      { name: 'c', state: 'missing' },
    ])
  })

  it('accepts field names the server accepts', () => {
    expect(FIELD_NAME.test('anmerkung')).toBe(true)
    expect(FIELD_NAME.test('note_2')).toBe(true)
    expect(FIELD_NAME.test('Note')).toBe(false)
    expect(FIELD_NAME.test('2note')).toBe(false)
  })

  it('marks loaded fields with their source and the rest as custom', () => {
    expect(originsFor({ passages: 'p', anmerkung: 'n' }, ['passages'], 'run')).toEqual({
      passages: 'run',
      anmerkung: 'custom',
    })
  })
})
