/**
 * Layout of the series pipeline canvas, kept out of the component so it can be
 * tested on its own.
 *
 * The planner's nodes stand in a column on the left, one lane per episode runs
 * to the right. Positions are deterministic — the canvas pans and zooms, but
 * nodes are never placed by hand — so two people looking at the same series
 * see the same picture.
 */

import type { RunGraphNodeOut, SeriesGraphOut } from '@/api/types'

export type CanvasMode = 'compact' | 'detail'

export interface Geometry {
  width: number
  height: number
  gapX: number
  lanePad: number
  laneLabel: number
  laneGap: number
  planGap: number
  planToLanes: number
}

export const GEOMETRY: Record<CanvasMode, Geometry> = {
  compact: {
    width: 174,
    height: 84,
    gapX: 36,
    lanePad: 12,
    laneLabel: 20,
    laneGap: 12,
    planGap: 26,
    planToLanes: 84,
  },
  detail: {
    width: 206,
    height: 262,
    gapX: 44,
    lanePad: 14,
    laneLabel: 22,
    laneGap: 16,
    planGap: 30,
    planToLanes: 96,
  },
}

/** Inputs that only a series run has; the canvas marks them as new. */
export const SERIES_INPUTS = new Set(['series_request', 'episode_brief', 'series_context'])

export interface PlacedNode {
  /** `${runId or 'pending-'+episode}:${node name}`, unique on the canvas. */
  key: string
  runId: string | null
  /** 0 for the planner, 1… for the episodes. */
  episode: number
  node: RunGraphNodeOut
  x: number
  y: number
}

export interface Lane {
  episode: number
  title: string
  runId: string | null
  x: number
  y: number
  width: number
  height: number
}

export interface Edge {
  kind: 'plain' | 'brief' | 'bus' | 'earlier'
  path: string
  label?: string
  labelX?: number
  labelY?: number
}

export interface CanvasLayout {
  nodes: PlacedNode[]
  lanes: Lane[]
  edges: Edge[]
  seeds: { x: number; y: number; keys: string[] }
  width: number
  height: number
}

const MARGIN = 16

function curve(ax: number, ay: number, bx: number, by: number): string {
  const dx = Math.max(24, (bx - ax) / 2)
  return `M${ax} ${ay} C${ax + dx} ${ay}, ${bx - dx} ${by}, ${bx} ${by}`
}

/** The nodes an episode lane shows: its run's flow without `ingest`, which the planner ran. */
export function laneNodes(nodes: RunGraphNodeOut[]): RunGraphNodeOut[] {
  return nodes.filter((node) => node.name !== 'ingest')
}

export function layoutCanvas(graph: SeriesGraphOut, mode: CanvasMode): CanvasLayout {
  const g = GEOMETRY[mode]
  const planNodes = graph.plan?.nodes ?? []
  const seedKeys = (graph.plan?.seeds ?? []).map((seed) => seed.key)
  const lanesData = graph.episodes.map((episode) => ({
    ...episode,
    nodes: laneNodes(episode.graph.nodes),
  }))
  const columns = Math.max(1, ...lanesData.map((lane) => lane.nodes.length))

  const laneHeight = g.height + g.lanePad * 2 + g.laneLabel
  const lanesHeight = lanesData.length * laneHeight + Math.max(0, lanesData.length - 1) * g.laneGap
  const planCount = planNodes.length + 1 // the seed card stands on top
  const planHeight = planCount * g.height + (planCount - 1) * g.planGap
  const laneTop = MARGIN + Math.max(0, (planHeight - lanesHeight) / 2)
  const planTop = MARGIN + Math.max(0, (lanesHeight - planHeight) / 2)
  const planX = MARGIN
  const colX = (index: number) => planX + g.width + g.planToLanes + index * (g.width + g.gapX)
  const rowY = (row: number) => laneTop + row * (laneHeight + g.laneGap) + g.laneLabel + g.lanePad
  const planY = (index: number) => planTop + index * (g.height + g.planGap)

  const nodes: PlacedNode[] = []
  const edges: Edge[] = []
  const lanes: Lane[] = []
  const planRun = graph.plan?.run_id ?? null

  planNodes.forEach((node, index) => {
    nodes.push({
      key: `${planRun ?? 'plan'}:${node.name}`,
      runId: planRun,
      episode: 0,
      node,
      x: planX,
      y: planY(index + 1),
    })
  })
  const planCenter = planX + g.width / 2
  for (let i = 0; i < planNodes.length; i += 1) {
    edges.push({ kind: 'plain', path: `M${planCenter} ${planY(i) + g.height} V${planY(i + 1) - 2}` })
  }
  const planOut = planNodes.length ? planY(planNodes.length) + g.height / 2 : planY(0) + g.height / 2

  lanesData.forEach((lane, row) => {
    const y = rowY(row)
    const x = colX(0) - g.lanePad
    lanes.push({
      episode: lane.index,
      title: lane.title,
      runId: lane.run_id,
      x,
      y: y - g.lanePad - g.laneLabel,
      width: colX(columns - 1) + g.width + g.lanePad - x,
      height: laneHeight,
    })
    lane.nodes.forEach((node, column) => {
      nodes.push({
        key: `${lane.run_id ?? `pending-${lane.index}`}:${node.name}`,
        runId: lane.run_id,
        episode: lane.index,
        node,
        x: colX(column),
        y,
      })
      if (column > 0) {
        edges.push({
          kind: 'plain',
          path: `M${colX(column - 1) + g.width} ${y + g.height / 2} H${colX(column) - 2}`,
        })
      }
    })
    if (planNodes.length && lane.nodes.length) {
      edges.push({
        kind: 'brief',
        path: curve(planX + g.width, planOut, colX(0) - 2, y + g.height / 2),
      })
    }
  })
  if (planNodes.length && lanesData.length) {
    edges.push({
      kind: 'brief',
      path: '',
      label: 'episode_brief',
      labelX: planX + g.width + 6,
      labelY: planOut + 16,
    })
  }

  // The script column: every outline feeds it (the bus), and each script hands
  // its full text down to the next episode's.
  const scriptColumn = lanesData[0]?.nodes.findIndex((node) =>
    node.consumes.some((port) => port.key === 'series_context'),
  )
  if (scriptColumn !== undefined && scriptColumn > 0 && lanesData.length > 1) {
    const busX = colX(scriptColumn) - g.gapX / 2
    edges.push({
      kind: 'bus',
      path: `M${busX} ${rowY(0) + g.height / 2 - 10} V${rowY(lanesData.length - 1) + g.height / 2 + 10}`,
    })
    const sx = colX(scriptColumn) + g.width - 18
    for (let row = 0; row < lanesData.length - 1; row += 1) {
      edges.push({
        kind: 'earlier',
        path: `M${sx} ${rowY(row) + g.height} V${rowY(row + 1) - 3}`,
      })
    }
  }

  const width = colX(columns - 1) + g.width + g.lanePad + MARGIN
  const height = MARGIN + Math.max(lanesHeight, planHeight) + MARGIN * 2
  return {
    nodes,
    lanes,
    edges,
    seeds: { x: planX, y: planY(0), keys: seedKeys },
    width,
    height,
  }
}

/** The scale and offset that show the whole layout inside a viewport. */
export function fitView(
  layout: { width: number; height: number },
  viewport: { width: number; height: number },
): { x: number; y: number; k: number } {
  const k = Math.max(
    0.3,
    Math.min(1, (viewport.width - 16) / layout.width, (viewport.height - 16) / layout.height),
  )
  return {
    k,
    x: Math.max(8, (viewport.width - layout.width * k) / 2),
    y: Math.max(8, (viewport.height - layout.height * k) / 2),
  }
}

/** Zoom by `factor` around a point, keeping that point still. */
export function zoomAt(
  view: { x: number; y: number; k: number },
  factor: number,
  cx: number,
  cy: number,
): { x: number; y: number; k: number } {
  const k = Math.max(0.3, Math.min(1.6, view.k * factor))
  return { k, x: cx - (cx - view.x) * (k / view.k), y: cy - (cy - view.y) * (k / view.k) }
}
