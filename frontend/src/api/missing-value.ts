import { request } from './client'
import type {
  DispositionEntry,
  MissingValueView,
  RecollectTask,
} from '@/data/types'

export type RecollectScope = 'confirmed' | 'pending'
export type ReviewDecision = 'new_format' | 'old_format' | 'cannot_confirm'

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = (await response.json()) as T & { message?: string }
  if (!response.ok) {
    throw new Error(payload?.message || `接口返回 ${response.status}，操作未生效`)
  }
  return payload
}

async function get<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error('缺值数据读取失败')
  return (await response.json()) as T
}

export function loadMissingValueView(params: Record<string, string> = {}) {
  const query = new URLSearchParams(params).toString()
  return get<MissingValueView>(`/api/missing-values?${query}`)
}

export function listCollectTasks() {
  return get<{ items: RecollectTask[]; total: number }>('/api/missing-values/tasks')
}

export function getCollectTask(taskId: number) {
  return get<RecollectTask>(`/api/missing-values/tasks/${taskId}`)
}

export type SubmitResult = {
  ok: boolean
  message: string
  task?: RecollectTask
  rejected?: { raw_record_id: string; reason: string }[]
  accepted?: number[]
}

export function submitRecollect(payload: {
  record_ids: number[]
  scope: RecollectScope
  batch_no?: string
  source_id?: string
  parser_version?: string
  operator?: string
  note?: string
}) {
  return post<SubmitResult>('/api/missing-values/recollect', payload)
}

export function runCollectTask(taskId: number, interruptAfter?: number) {
  const query = interruptAfter === undefined ? '' : `?interrupt_after=${interruptAfter}`
  return post<SubmitResult>(`/api/missing-values/tasks/${taskId}/run${query}`, {})
}

export function retryCollectTask(taskId: number, interruptAfter?: number) {
  const query = interruptAfter === undefined ? '' : `?interrupt_after=${interruptAfter}`
  return post<SubmitResult>(`/api/missing-values/tasks/${taskId}/retry${query}`, {})
}

export function verifyCollectItems(
  taskId: number,
  decisions: {
    raw_record_id: number
    decision: ReviewDecision
    parser_version?: string
    operator?: string
    note?: string
  }[],
) {
  return post<SubmitResult & { updated?: unknown[] }>(
    `/api/missing-values/tasks/${taskId}/verify`,
    { decisions },
  )
}

export function reparseCollected(taskId: number) {
  return post<SubmitResult & { created_entity_ids?: number[]; skipped_raw_ids?: number[] }>(
    `/api/missing-values/tasks/${taskId}/reparse`,
    {},
  )
}

export function writeDisposition(taskId: number, operator = '值班员') {
  return post<SubmitResult & { entries?: DispositionEntry[]; skipped_raw_ids?: number[] }>(
    `/api/missing-values/tasks/${taskId}/disposition`,
    { operator },
  )
}

export function listDispositions() {
  return get<{ items: DispositionEntry[]; total: number }>(
    '/api/missing-values/dispositions',
  )
}
