/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 缺值分布视图与补采流程 */
export type FieldMissing = {
  field: string
  state: 'confirmed' | 'candidate'
}

export type MissingValueRow = {
  id: number
  raw_record_id: number
  parsed_entity_id: number | null
  batch_no: string
  source_id: string
  origin_label: string
  parser_version: string
  parser_version_confirmed: boolean
  historical_parser_version: string
  verified_parser_version: string
  line_no: string
  raw_body: string
  source_format: 'new' | 'old' | 'unknown'
  format_label: string
  format_confidence: string
  field_values: Record<string, string>
  required_fields: string[]
  optional_fields: string[]
  field_missing: FieldMissing[]
  missing_fields: string[]
  candidate_fields: string[]
  conclusion_status: string
  conclusion_type: string
  conclusion_summary: string
  conclusion_id: number
  rule_version_note: string
  bucket: string
  view_status: string
  selectable: boolean
  blocked_reason: string
  queued: boolean
  task_id: number | null
  task_no: string
  recovered: boolean
  recovered_fields: Record<string, string>
  reparse_status: string
  status: string
  pending: boolean
  abnormal: boolean
}

export type MissingValueGroup = {
  source_id: string
  origin_label: string
  versions: {
    parser_version: string
    parser_version_confirmed: boolean
    rule_version_note: string
    format_labels: string[]
    rows: MissingValueRow[]
  }[]
}

export type MissingValueView = {
  generated_at: string
  bucket: string
  buckets: Record<string, number>
  field_stats: {
    field: string
    confirmed: number
    candidate: number
    records: number[]
  }[]
  groups: MissingValueGroup[]
  rows: MissingValueRow[]
  total: number
}

export type RecollectItem = {
  raw_record_id: number
  line_no: string
  raw_body: string
  batch_no: string
  source_id: string
  parser_version: string
  quality_conclusion_id: number | null
  conclusion_status: string
  conclusion_type: string
  conclusion_summary: string
  conclusion_rule_note: string
  historical_parser_version: string
  current_parser_version: string
  classification: 'confirmed' | 'pending' | 'not_required'
  missing_fields: string[]
  status: string
  verified: boolean | null
  recovered_fields: Record<string, string>
  recovered_value: string
  recovered_at: string
  review_conclusion: string
  disposition_id: number | null
  reparsed_entity_id: number | null
  downstream_ready: boolean
  field_missing: FieldMissing[]
}

export type RecollectTask = {
  id: number
  task_no: string
  status: string
  task_status: string
  source_id: string
  batch_no: string
  parser_version: string
  mode: 'confirmed' | 'pending'
  quality_state: string
  downstream_allowed: boolean
  scope: number[]
  scope_count: number
  items: RecollectItem[]
  attempts: number
  submitted_at: string
  submitted_by: string
  last_retry_at: string
  interrupted_at: string
  completed_at: string
  note: string
  dispositions?: DispositionEntry[]
}

export type DispositionEntry = {
  id: number
  task_no: string
  raw_record_id: number
  decision: string
  downstream_ready: boolean
  downstream_scope: string
  field_conclusions: {
    field: string
    required: boolean
    value: string
    missing: boolean
    state: string
  }[]
  missing_fields: string[]
  parser_version: string
  historical_parser_version: string
  verified_at: string
  written_at: string
  written_by: string
}

