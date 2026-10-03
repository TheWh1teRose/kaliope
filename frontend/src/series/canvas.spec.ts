import { describe, expect, it } from 'vitest'

import { fitView, layoutCanvas, zoomAt } from './canvas'
import { seriesGraph } from './fixtures'

describe('series canvas layout', () => {
  it('puts the planner in a column and every episode in its own lane', () => {
    const layout = layoutCanvas(seriesGraph(), 'compact')
    const plan = layout.nodes.filter((n) => n.episode === 0)
    expect(plan.map((n) => n.node.name)).toEqual(['ingest', 'content_budget', 'series_plan'])
    expect(new Set(plan.map((n) => n.x)).size).toBe(1)
    expect(layout.lanes.map((l) => l.episode)).toEqual([1, 2, 3])
    const second = layout.nodes.filter((n) => n.episode === 2)
    expect(second.map((n) => n.node.name)).toEqual(['content_budget', 'select', 'outline', 'script'])
    expect(new Set(second.map((n) => n.y)).size).toBe(1)
    expect(second[0].key).toBe('r2:content_budget')
  })

  it('draws the brief to every lane, the outline bus and the earlier text', () => {
    const kinds = layoutCanvas(seriesGraph(), 'compact').edges.map((e) => e.kind)
    expect(kinds.filter((k) => k === 'brief')).toHaveLength(4) // three curves and the label
    expect(kinds.filter((k) => k === 'bus')).toHaveLength(1)
    expect(kinds.filter((k) => k === 'earlier')).toHaveLength(2)
  })

  it('is larger in the detailed view', () => {
    const compact = layoutCanvas(seriesGraph(), 'compact')
    const detail = layoutCanvas(seriesGraph(), 'detail')
    expect(detail.height).toBeGreaterThan(compact.height)
    expect(detail.width).toBeGreaterThan(compact.width)
  })

  it('fits the layout into the viewport and zooms around a point', () => {
    const view = fitView({ width: 2000, height: 500 }, { width: 1016, height: 600 })
    expect(view.k).toBeCloseTo(0.5)
    const zoomed = zoomAt({ x: 0, y: 0, k: 1 }, 2, 100, 100)
    expect(zoomed).toEqual({ k: 1.6, x: -60, y: -60 })
  })
})
