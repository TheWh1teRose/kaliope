export type BBox = [number, number, number, number]

export interface User {
  id: string
  email: string
  name: string
  role: 'admin' | 'reviewer'
  active: boolean
}

export interface IngestionReport {
  language: string
  language_confidence: number
  page_count: number
  extractable_words: number
  narratable_words: number
  visual_content_ratio: number
  text_density: number
  structure_source: 'outline' | 'typographic' | 'flat'
  structure_confidence: 'high' | 'medium' | 'low'
  section_count: number
  zone_distribution: Record<string, number>
  zone_uncertain_ratio: number
  table_count: number
  boilerplate_lines_removed: number
  anchor_integrity: number
  reading_order_confidence: number
  warnings: string[]
  ingestion_confidence: 'high' | 'medium' | 'low'
  confidence_reasons: string[]
}

export interface DocumentSummary {
  id: string
  filename: string
  sha256: string
  title: string | null
  uploaded_at: string
  uploaded_by: string | null
  page_count: number | null
  language: string | null
  parse_version: number
  parse_status: 'pending' | 'parsing' | 'parsed' | 'failed'
  parse_error: string | null
  report: IngestionReport | null
  /** `null` is the root of the folder tree. */
  folder_id: string | null
}

export interface FolderOut {
  id: string
  name: string
  parent_id: string | null
  depth: number
  path: string[]
  document_count: number
  total_document_count: number
  created_at: string
}

export interface ZoneSpec {
  zone: string
  meaning: string
  narratable: boolean
  context_only: boolean
  salience: number
}

export interface BlockOut {
  id: string
  ordinal: number
  text: string
  page: number
  bboxes: [number, BBox][]
  zone: string
  zone_confidence: number
  zone_uncertain: boolean
  salience: number
  section_id: string | null
  heading_level: number | null
  is_table: boolean
  zone_overridden_from: string | null
}

export interface SectionOut {
  id: string
  title: string
  level: number
  ordinal: number
  parent_id: string | null
  block_ids: string[]
  page_start: number | null
  page_end: number | null
}

export interface StructureOut {
  document_id: string
  parse_version: number
  language: string
  page_count: number
  page_sizes: [number, number][]
  sections: SectionOut[] | null
  blocks: BlockOut[]
  objectives: string[]
  zone_catalogue: ZoneSpec[]
}

export interface SpeakerSpec {
  id: string
  name: string
  role: string
  voice_note: string | null
}

export interface FormatSpec {
  id: string
  name: string
  speakers: SpeakerSpec[]
  register: string
  target_minutes: number
  opening: string | null
  closing: string | null
  beats_hint: string | null
}

export interface AudienceSpec {
  description: string
  prior_knowledge?: string | null
  role?: string | null
  listening_context?: string | null
  desired_outcome?: string | null
}

export interface Violation {
  target_id: string | null
  message: string
  detail: Record<string, unknown>
}

export type GateStatus = 'pass' | 'warn' | 'fail' | 'skipped'

export interface GateReport {
  id: string
  name: string
  status: GateStatus
  violations: Violation[]
  skip_reason: string | null
  /** The numbers the gate computed, reported whatever the outcome. */
  measurements: Record<string, unknown>
}

export interface GateSpec {
  id: string
  name: string
  severity: 'fail' | 'warn'
  inspects: string[]
  rule: string
  method: string
  thresholds: Record<string, unknown>
  skip_condition: string | null
  uses_model: boolean
}

export interface GateDetail {
  spec: GateSpec
  report: GateReport | null
}

export interface RunNodeOut {
  node_name: string
  node_version: string
  cache_hit: boolean
  artifact_hash: string | null
  cost_usd: number
  tokens_in: number
  tokens_out: number
  model_id: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export type RunStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'in_review'
  | 'reviewed'

export type NoteSubject = 'outline' | 'script'
export type NoteTargetKind = 'beat' | 'segment'
export type NoteSource = 'ai_critic' | 'human'

export interface NoteTarget {
  kind: NoteTargetKind
  id: string
}

export interface Note {
  id: string
  text: string
  target: NoteTarget
  source: NoteSource
  criterion: string | null
}

export interface BeatOut {
  id: string
  title: string
  block_ids: string[]
  word_budget: number
  goal_id: string | null
  summary: string | null
}

export interface OutlineOut {
  beats: BeatOut[]
}

export interface ScriptSegment {
  id: string
  speaker: string
  text: string
  kind: string
  beat_id: string
}

export interface ScriptDraft {
  segments: ScriptSegment[]
}

export interface PauseOut {
  node: string
  subject: NoteSubject
  instructions: string
}

export interface FeedbackCitation {
  id: string
  speaker: string
  text: string
  kind: string
  beat_id: string | null
  anchors: AnchorOut[]
}

export interface FeedbackOut {
  run_id: string
  kind: 'run' | 'bench'
  node: string
  status: string
  subject: NoteSubject
  instructions: string
  document_id: string | null
  outline: OutlineOut | null
  script: ScriptDraft | null
  citations: FeedbackCitation[]
  existing_notes: Note[]
  draft_notes: Note[]
}

export interface RunOut {
  id: string
  document_id: string
  document_title: string | null
  flow_id: string
  flow_version: string
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  total_cost_usd: number
  target_minutes: number | null
  verdict: string | null
  format_spec: FormatSpec | null
  audience_spec: AudienceSpec | null
  nodes: RunNodeOut[]
  gates: GateReport[]
  manifest: Record<string, unknown> | null
  pause: PauseOut | null
}

export interface AnchorRect {
  page: number
  bbox: BBox
}

export interface AnchorOut {
  block_id: string
  char_start: number
  char_end: number
  text: string
  resolved: boolean
  rects: AnchorRect[]
}

export interface SegmentComment {
  id: string
  user_id: string
  user_email: string | null
  note: string
  created_at: string
}

export interface SegmentOut {
  id: string
  ordinal: number
  speaker: string
  text: string
  kind: 'claim' | 'pedagogy'
  beat_id: string | null
  anchors: AnchorOut[]
  violations: { gate: string; status: string; message: string }[]
  accepted: boolean
  flagged: boolean
  edited: boolean
  /** The generated text, kept alongside an edit so both can be shown. */
  original_text: string | null
  /** True when there is a review action an undo can take back. */
  undoable: boolean
  tags: string[]
  comments: SegmentComment[]
}

export interface ScriptOut {
  run_id: string
  document_id: string
  parse_version: number
  language: string
  format_spec: FormatSpec
  segments: SegmentOut[]
  word_count: number
}

export interface FlowOut {
  id: string
  version: string
  description: string | null
  nodes: string[]
  gates: string[]
}

// --------------------------------------------------------------- flow graph

export interface FieldOut {
  name: string
  type: string
  required: boolean
  description: string | null
}

export interface PortOut {
  key: string
  model: string
  fields: FieldOut[]
  produced_by: string | null
}

export interface EdgeOut {
  from_node: string | null
  to_node: string
  key: string
}

export interface FlowNodeOut {
  name: string
  version: string
  description: string | null
  consumes: PortOut[]
  produces: PortOut
  config: Record<string, unknown>
  checked_by: string[]
}

export interface FlowGraphOut {
  flow_id: string
  flow_version: string
  description: string | null
  nodes: FlowNodeOut[]
  edges: EdgeOut[]
  seeds: PortOut[]
  gates: GateSpec[]
}

export type NodeRunStatus =
  | 'pending'
  | 'running'
  | 'cached'
  | 'ok'
  | 'failed'
  | 'blocked'
  | 'paused'

export interface NodeFinding {
  gate: string
  name: string
  status: GateStatus
  violations: number
}

export interface RunGraphNodeOut extends FlowNodeOut {
  status: NodeRunStatus
  cache_hit: boolean
  artifact_hash: string | null
  model_id: string | null
  tokens_in: number
  tokens_out: number
  cost_usd: number
  wall_ms: number | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  output_summary: Record<string, unknown>
  findings: NodeFinding[]
}

export interface RunGraphOut {
  run_id: string
  flow_id: string
  flow_version: string
  status: RunStatus
  nodes: RunGraphNodeOut[]
  edges: EdgeOut[]
  seeds: PortOut[]
  failed_node: string | null
  error: string | null
  total_cost_usd: number
}

export interface NodeValueOut {
  key: string
  model: string
  produced_by: string | null
  artifact_hash: string | null
  summary: Record<string, unknown>
  preview: string | null
  truncated: boolean
  available: boolean
}

export interface NodeIOOut {
  run_id: string
  node_name: string
  node_version: string
  status: NodeRunStatus
  config: Record<string, unknown>
  cache_key: string | null
  cache_hit: boolean
  model_id: string | null
  tokens_in: number
  tokens_out: number
  cost_usd: number
  wall_ms: number | null
  error: string | null
  inputs: NodeValueOut[]
  output: NodeValueOut | null
}

export interface ReasonCodeSpec {
  code: string
  label_de: string
  requires_note: boolean
}

export interface ReviewSummary {
  run_id: string
  reviewer_id: string
  duration_seconds: number
  event_count: number
  reason_code_counts: Record<string, number>
  action_counts: Record<string, number>
  edited_script_artifact: string | null
}

export type EditAction =
  | 'accept'
  | 'edit'
  | 'flag'
  | 'comment'
  | 'relabel'
  | 'undo'
  | 'tag'
  | 'untag'

export interface EditEventIn {
  target_type: 'segment' | 'selection' | 'outline' | 'block_zone'
  target_id: string
  action: EditAction
  reason_code?: string | null
  note?: string | null
  text_before?: string | null
  text_after?: string | null
}

// ----------------------------------------------------- pipeline authoring

export type ParamType =
  | 'string'
  | 'text'
  | 'prompt'
  | 'int'
  | 'float'
  | 'bool'
  | 'model'
  | 'select'

export interface NodeParam {
  key: string
  label: string
  type: ParamType
  default: unknown
  description: string | null
  options: string[]
  minimum: number | null
  maximum: number | null
  advanced: boolean
}

export interface NodeDoc {
  summary: string
  detail: string[]
  inputs: Record<string, string>
  output: string | null
  failure_modes: string[]
  cost: string | null
}

export interface NodeSpecOut {
  name: string
  title: string
  version: string
  doc: NodeDoc
  params: NodeParam[]
  consumes: string[]
  produces: string
  input_model: string
  output_model: string
  output_fields: FieldOut[]
  checked_by: string[]
}

export interface NodeCatalogueOut {
  nodes: NodeSpecOut[]
  seeds: PortOut[]
  required_outputs: string[]
  models: string[]
}

export interface FlowNodeIn {
  node: string
  config: Record<string, unknown>
}

export interface PipelineDraft {
  name: string | null
  version: string
  description: string | null
  nodes: FlowNodeIn[]
  gates: string[]
}

export interface Wiring {
  key: string
  source: string | null
  from_seed: boolean
  satisfied: boolean
}

export interface NodeCheck {
  node: string
  position: number
  exists: boolean
  produces: string | null
  wiring: Wiring[]
  missing: string[]
  unknown_config: string[]
}

export interface FlowValidation {
  valid: boolean
  errors: string[]
  warnings: string[]
  nodes: NodeCheck[]
  seeds: string[]
}

export interface RevisionOut {
  revision: number
  version: string
  note: string | null
  restored_from: number | null
  created_at: string
  created_by: string | null
  created_by_email: string | null
  spec: Record<string, unknown> | null
}

export interface PipelineSummary {
  id: string
  name: string
  version: string
  revision: number
  description: string | null
  origin: 'file' | 'user'
  archived: boolean
  editable: boolean
  nodes: string[]
  gates: string[]
  updated_at: string | null
  updated_by_email: string | null
  run_count: number
  valid: boolean
}

export interface PipelineDetail extends PipelineSummary {
  definition: PipelineDraft
  validation: FlowValidation
}

export interface FormatSummary {
  id: string
  name: string
  revision: number
  origin: 'file' | 'user'
  archived: boolean
  speakers: number
  target_minutes: number
  register: string
  updated_at: string | null
  updated_by_email: string | null
  run_count: number
}

export interface FormatDetail extends FormatSummary {
  spec: FormatSpec
}

// --------------------------------------------------------------- node bench

export interface ValueKeyOut {
  key: string
  model: string
  produced_by: string[]
  consumed_by: string[]
  json_schema: Record<string, unknown>
  template: unknown
  seed: boolean
}

export interface BenchCatalogueOut {
  values: ValueKeyOut[]
  models: string[]
  default_format: FormatSpec | null
  default_audience: AudienceSpec | null
}

export interface BenchValueOut {
  key: string
  model: string
  produced_by: string | null
  artifact_hash: string | null
  summary: Record<string, unknown>
  preview: string | null
  truncated: boolean
  available: boolean
  source: string
  source_id: string | null
  payload: unknown | null
}

export interface BenchLoadOut {
  values: BenchValueOut[]
  nodes: FlowNodeIn[] | null
  document_id: string | null
}

export interface BenchNodeOut {
  node_name: string
  node_version: string
  cache_hit: boolean
  artifact_hash: string | null
  cost_usd: number
  tokens_in: number
  tokens_out: number
  model_id: string | null
  started_at: string | null
  finished_at: string | null
  wall_ms: number | null
  error: string | null
  status: NodeRunStatus
}

export interface LLMTraceOut {
  node_name: string | null
  model: string
  system: string | null
  messages: { role: string; content: string }[]
  temperature: number | null
  max_tokens: number
  response_text: string
  latency_ms: number
  tokens_in: number
  tokens_out: number
  cost_usd: number
  warnings: string[]
}

export interface BenchRunOut {
  id: string
  document_id: string | null
  document_title: string | null
  nodes: string[]
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  total_cost_usd: number
  verdict: string | null
  force: boolean
  records: BenchNodeOut[]
  values: BenchValueOut[]
  llm_traces: LLMTraceOut[]
  pause: PauseOut | null
}

export interface BenchRunIn {
  document_id?: string | null
  nodes: FlowNodeIn[]
  seeds: Record<string, { payload?: unknown; artifact_hash?: string }>
  force?: boolean
  only?: string | null
  from_node?: string | null
}
