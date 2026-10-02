import { describe, expect, it } from 'vitest'

import type { ExperimentOutput } from '@/api/types'

import {
  type VSDraft,
  type VSOutput,
  blindLetter,
  blindOrder,
  citationBadge,
  collectedDraft,
  costFacts,
  draftBadges,
  drafts,
  probabilityText,
  tally,
} from './vs'

function draft(item: string, extra: Partial<VSDraft> = {}): VSDraft {
  const [source, index] = item.split(':')
  return {
    item,
    source: source as VSDraft['source'],
    index: Number(index),
    probability: source === 'vs' ? 0.2 : null,
    segments: [{ speaker: 'Moderator', text: 'Hallo', kind: 'pedagogy', citations: [] }],
    words: 340,
    claims: 4,
    pedagogy: 3,
    citations: { cited: 4, located: 3, unknown_block: 0, unlocated: 1 },
    flags: [],
    ...extra,
  }
}

const output: VSOutput = {
  variant: 'standard',
  k: 3,
  word_budget: 350,
  citation_check: true,
  vs: { drafts: [draft('vs:0'), draft('vs:1'), draft('vs:2')], warnings: [], error: null },
  baseline: { drafts: [draft('baseline:0')], warnings: [], error: null },
  cost: {
    vs_usd: 0.162,
    baseline_usd: 0.035,
    vs_calls: 1,
    baseline_calls: 1,
    tokens_in: 4000,
    tokens_out: 6000,
    latency_ms: 74000,
    vs_per_draft_usd: 0.054,
    ratio_to_single_baseline: 4.63,
  },
}

function collected(item: string, runId: string, source: string): ExperimentOutput<unknown> {
  return {
    id: `${runId}-${item}`,
    experiment_key: 'verbalized_sampling',
    run_id: runId,
    item,
    label: null,
    output: output as unknown as Record<string, unknown>,
    text: null,
    meta: { vs_source: source },
    setup: {},
    created_at: '2026-10-01T12:00:00Z',
    folder_id: null,
    folder_path: [],
    created_by: null,
    status: null,
    note: null,
    decided_by: null,
    decided_at: null,
  }
}

describe('Verbalized Sampling helpers', () => {
  it('lists VS drafts before the plain one', () => {
    expect(drafts(output).map((d) => d.item)).toEqual(['vs:0', 'vs:1', 'vs:2', 'baseline:0'])
    expect(drafts(null)).toEqual([])
  })

  it('shuffles blind with a stable order per run', () => {
    const items = drafts(output).map((d) => d.item)
    const first = blindOrder('run-1', items)
    expect(blindOrder('run-1', [...items].reverse())).toEqual(first)
    expect([...first].sort()).toEqual([...items].sort())
    const orders = new Set(
      ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => blindOrder(id, items).join()),
    )
    expect(orders.size).toBeGreaterThan(1)
    expect(blindLetter('run-1', output, first[2])).toBe('C')
  })

  it('formats badges without revealing the method', () => {
    const badges = draftBadges(draft('vs:0'), 350).map((b) => b.text)
    expect(badges).toEqual(['340 / 350 Wörter', '4 Aussagen · 3 Didaktik'])
    expect(badges.join()).not.toMatch(/VS|0,2/)
    expect(draftBadges(draft('vs:0'), null)[0].text).toBe('340 / ? Wörter')
    expect(citationBadge(draft('vs:0'))).toEqual({ text: 'Belege 3/4', ok: false })
    expect(citationBadge(draft('vs:0', { citations: null }))).toBeNull()
    expect(probabilityText(null)).toBe('–')
    expect(probabilityText(0.2)).toBe('0,20')
  })

  it('shows the real cost against one plain call', () => {
    expect(costFacts(output.cost, 3)).toEqual([
      '4.000 / 6.000 Tokens',
      'VS $0.1620 (3 Fassungen, $0.0540/Fassung)',
      'ohne VS $0.0350',
      'Verhältnis 4,63×',
      '74,0 s',
    ])
  })

  it('counts collected drafts by method, unrevealed runs as blind', () => {
    const outputs = [
      collected('vs:0', 'r1', 'vs'),
      collected('vs:1', 'r1', 'vs'),
      collected('baseline:0', 'r1', 'baseline'),
      collected('vs:2', 'r2', 'vs'),
    ]
    expect(tally(outputs, new Set(['r1']))).toEqual({ vs: 2, baseline: 1, blind: 1 })
    expect(tally(outputs, new Set())).toEqual({ vs: 0, baseline: 0, blind: 4 })
    expect(collectedDraft(outputs[2])?.source).toBe('baseline')
  })
})
