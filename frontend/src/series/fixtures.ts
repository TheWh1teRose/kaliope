/** A small series graph for the canvas and view specs. */
import type { RunGraphNodeOut, RunGraphOut, SeriesGraphOut } from '@/api/types'

function node(name: string, status: RunGraphNodeOut['status'], consumes: string[] = []): RunGraphNodeOut {
  return {
    name,
    version: '1.0',
    description: null,
    consumes: consumes.map((key) => ({ key, model: 'X', fields: [], produced_by: null })),
    produces: { key: name, model: 'Y', fields: [], produced_by: name },
    config: {},
    checked_by: [],
    status,
    cache_hit: false,
    artifact_hash: null,
    model_id: null,
    tokens_in: 100,
    tokens_out: 20,
    cost_usd: 0.01,
    wall_ms: 1500,
    started_at: null,
    finished_at: null,
    error: null,
    output_summary: {},
    findings: [],
  }
}

function graph(runId: string, nodes: RunGraphNodeOut[]): RunGraphOut {
  return {
    run_id: runId,
    flow_id: 'baseline_v0',
    flow_version: '1.0',
    status: 'running',
    nodes,
    edges: [],
    seeds: [],
    failed_node: null,
    error: null,
    total_cost_usd: 0,
  }
}

function episode(scriptStatus: RunGraphNodeOut['status']): RunGraphNodeOut[] {
  return [
    node('ingest', 'cached'),
    node('content_budget', 'ok', ['parsed', 'episode_brief']),
    node('select', 'ok', ['parsed', 'episode_brief']),
    node('outline', 'ok', ['selection', 'episode_brief']),
    node('script', scriptStatus, ['outline', 'series_context']),
  ]
}

export function seriesGraph(): SeriesGraphOut {
  return {
    series_id: 's1',
    status: 'writing',
    plan: {
      ...graph('plan', [node('ingest', 'cached'), node('content_budget', 'ok'), node('series_plan', 'ok')]),
      seeds: [
        { key: 'document_ref', model: 'DocumentRef', fields: [], produced_by: null },
        { key: 'series_request', model: 'SeriesRequest', fields: [], produced_by: null },
      ],
    },
    episodes: [
      { index: 1, title: 'Eins', run_id: 'r1', graph: graph('r1', episode('ok')) },
      { index: 2, title: 'Zwei', run_id: 'r2', graph: graph('r2', episode('running')) },
      { index: 3, title: 'Drei', run_id: 'r3', graph: graph('r3', episode('pending')) },
    ],
  }
}
