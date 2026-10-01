import { describe, expect, it } from 'vitest'

import { textBlocks } from './outputText'

describe('readable view of an answer', () => {
  it('reads speaker/text lists as dialogue with kind and citations', () => {
    const blocks = textBlocks({
      segments: [
        { speaker: 'Moderator', text: 'Was passiert?', kind: 'pedagogy', citations: [] },
        {
          speaker: 'Expertin',
          text: 'Die Lichtreaktion.',
          kind: 'claim',
          citations: [{ block_id: 'b14', quote: 'Die Lichtreaktion findet statt.' }],
        },
      ],
    })
    expect(blocks).toEqual([
      {
        type: 'dialogue',
        key: 'segments',
        lines: [
          { speaker: 'Moderator', text: 'Was passiert?', kind: 'pedagogy', citations: [] },
          {
            speaker: 'Expertin',
            text: 'Die Lichtreaktion.',
            kind: 'claim',
            citations: [{ blockId: 'b14', quote: 'Die Lichtreaktion findet statt.' }],
          },
        ],
      },
    ])
  })

  it('keeps a changed structure readable as labelled fields', () => {
    const blocks = textBlocks({ hook: 'Ein Satz.', words: 120, extra: { a: 1 } })
    expect(blocks.map((b) => b.type)).toEqual(['field', 'field', 'json'])
    expect(blocks[0]).toEqual({ type: 'field', key: 'hook', value: 'Ein Satz.' })
  })

  it('falls back to the raw text when there is no parsed answer', () => {
    expect(textBlocks(null, 'Moderator: Hallo')).toEqual([{ type: 'prose', text: 'Moderator: Hallo' }])
    expect(textBlocks(null, '   ')).toEqual([])
  })
})
