<template>
  <section class="page" data-module="missing_value_view">
    <header class="page-head">
      <div>
        <h2>缺值分布</h2>
        <p class="page-desc">
          按来源与解析版本排列原始行，分开呈现确认需要补采的日志和还待核对的缺值；
          待核对的记录不能作为已确认缺值发往下游。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="mv-refresh" type="button" @click="reloadAll">
          刷新视图
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article
        v-for="card in bucketCards"
        :key="card.key"
        class="stat-card mv-stat"
        :class="{ active: activeBucket === card.key }"
        :data-testid="`mv-bucket-${card.key}`"
        @click="switchBucket(card.key)"
      >
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.count }}</strong>
        <small class="mv-hint">{{ card.hint }}</small>
      </article>
    </div>

    <div class="mv-layout">
      <div class="mv-main">
        <form class="mv-toolbar" @submit.prevent="applyFilters">
          <label class="mv-field">
            <span>采集批次</span>
            <input v-model="filters.batch_no" data-testid="mv-filter-batch" placeholder="INGE-1001" />
          </label>
          <label class="mv-field">
            <span>来源标识</span>
            <input v-model="filters.source_id" data-testid="mv-filter-source" placeholder="全部来源" />
          </label>
          <label class="mv-field">
            <span>解析版本</span>
            <input
              v-model="filters.parser_version"
              data-testid="mv-filter-version"
              placeholder="全部版本"
            />
          </label>
          <button class="btn" data-testid="mv-query" type="submit">查询</button>
          <button class="btn ghost" data-testid="mv-reset" type="button" @click="resetFilters">
            重置
          </button>
        </form>

        <p v-if="errorMessage" class="error-text" data-testid="mv-error">{{ errorMessage }}</p>
        <p v-if="notice" class="mv-notice" data-testid="mv-notice">{{ notice }}</p>

        <div v-for="group in view.groups" :key="group.source_id" class="mv-group">
          <header class="mv-group-head">
            <div>
              <strong>{{ group.origin_label }}</strong>
              <span class="mv-muted">来源 {{ group.source_id }}</span>
            </div>
            <span class="mv-muted">{{ group.versions.length }} 个解析版本</span>
          </header>

          <section
            v-for="version in group.versions"
            :key="`${group.source_id}-${version.parser_version}`"
            class="mv-version"
            :data-testid="`mv-version-${version.parser_version}`"
          >
            <header class="mv-version-head">
              <span class="mv-version-name">{{ version.parser_version }}</span>
              <span class="mv-tag" :class="version.parser_version_confirmed ? 'ok' : 'warn'">
                {{ version.parser_version_confirmed ? '版本口径已确认' : '版本口径待核对' }}
              </span>
              <span v-for="label in version.format_labels" :key="label" class="mv-tag">
                {{ label }}
              </span>
              <span class="mv-muted mv-rule-note">{{ version.rule_version_note }}</span>
            </header>

            <table class="data-table mv-table">
              <thead>
                <tr>
                  <th class="mv-check">
                    <input
                      type="checkbox"
                      :checked="allSelected(version.rows)"
                      :disabled="!selectableRows(version.rows).length"
                      @change="toggleVersion(version.rows, ($event.target as HTMLInputElement).checked)"
                    />
                  </th>
                  <th>行号</th>
                  <th>原始行</th>
                  <th>格式</th>
                  <th>字段口径</th>
                  <th>缺值</th>
                  <th>视图分类</th>
                  <th>质量结论</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in version.rows"
                  :key="row.raw_record_id"
                  :data-testid="`mv-row-${row.raw_record_id}`"
                  :data-bucket="row.bucket"
                  :data-format="row.source_format"
                  :class="`mv-row bucket-${bucketClass(row.bucket)}`"
                >
                  <td class="mv-check">
                    <input
                      v-model="selected"
                      type="checkbox"
                      :value="row.raw_record_id"
                      :disabled="!row.selectable"
                      :title="row.blocked_reason"
                      :data-testid="`mv-select-${row.raw_record_id}`"
                    />
                  </td>
                  <td>{{ row.line_no }}</td>
                  <td class="mv-body">{{ row.raw_body || '—' }}</td>
                  <td>
                    <span class="mv-tag" :class="`fmt-${row.source_format}`">
                      {{ row.format_label }}
                    </span>
                  </td>
                  <td class="mv-scope">
                    <span>必填：{{ row.required_fields.join('、') || '—' }}</span>
                    <span>可空：{{ row.optional_fields.join('、') || '无' }}</span>
                    <span
                      v-if="row.verified_parser_version && row.verified_parser_version !== row.parser_version"
                      class="mv-effective"
                    >
                      核对后按 {{ row.verified_parser_version }} 重新判定
                    </span>
                  </td>
                  <td>
                    <span
                      v-for="entry in row.field_missing"
                      :key="entry.field"
                      class="mv-missing"
                      :class="entry.state"
                    >
                      {{ entry.field }}（{{ entry.state === 'confirmed' ? '已确认缺值' : '待核对' }}）
                    </span>
                    <span v-if="!row.field_missing.length" class="mv-muted">无</span>
                  </td>
                  <td>
                    <span class="mv-badge" :class="bucketClass(row.bucket)">{{ row.bucket }}</span>
                    <small v-if="row.blocked_reason" class="mv-blocked">{{ row.blocked_reason }}</small>
                  </td>
                  <td class="mv-conclusion" :data-testid="`mv-conclusion-${row.raw_record_id}`">
                    <strong>#{{ row.conclusion_id }} {{ row.conclusion_type }}</strong>
                    <small>{{ row.conclusion_summary }}</small>
                    <small class="mv-muted">{{ row.rule_version_note }}</small>
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>

        <p v-if="!view.groups.length" class="empty-state mv-empty" data-testid="mv-empty">
          当前筛选条件下没有缺值记录
        </p>

        <section class="mv-submit" data-testid="mv-submit-panel">
          <header class="mv-submit-head">
            <strong>从缺值分布视图发起补采</strong>
            <span class="mv-muted">已选择 {{ selected.length }} 条原始行</span>
          </header>
          <div class="mv-submit-actions">
            <button
              class="btn primary"
              type="button"
              data-testid="mv-submit-confirmed"
              :disabled="submitting"
              @click="submitScope('confirmed')"
            >
              提交已确认缺值补采
            </button>
            <button
              class="btn"
              type="button"
              data-testid="mv-submit-pending"
              :disabled="submitting"
              @click="submitScope('pending')"
            >
              提交待核对范围
            </button>
            <span class="mv-muted">
              待核对范围只做核实，不能作为已确认缺值下发；补采详情会关联对应的历史质量结论。
            </span>
          </div>
          <p class="mv-muted" data-testid="mv-selection-summary">
            已选行：{{ selected.length ? selected.join('、') : '无' }}；当前视图分类：
            {{ activeBucket || '全部' }}
          </p>
        </section>
      </div>

      <aside class="mv-side">
        <section class="mv-panel" data-testid="mv-field-stats">
          <h3>字段级缺值分布</h3>
          <table class="data-table">
            <thead>
              <tr><th>字段</th><th>已确认</th><th>待核对</th><th>涉及行</th></tr>
            </thead>
            <tbody>
              <tr v-for="stat in view.field_stats" :key="stat.field">
                <td>{{ stat.field }}</td>
                <td>{{ stat.confirmed }}</td>
                <td>{{ stat.candidate }}</td>
                <td class="mv-muted">{{ stat.records.join('、') }}</td>
              </tr>
              <tr v-if="!view.field_stats.length">
                <td colspan="4" class="empty-state">暂无字段级缺值</td>
              </tr>
            </tbody>
          </table>
          <p class="mv-muted">
            字段口径按每条记录对应的历史解析版本判定：旧版 host 可空，新版 host 必填。
          </p>
        </section>

        <section class="mv-panel" data-testid="mv-task-panel">
          <h3>补采任务 / 重试队列</h3>
          <article
            v-for="task in tasks"
            :key="task.id"
            class="mv-task"
            :data-testid="`mv-task-${task.id}`"
          >
            <header class="mv-task-head">
              <strong>{{ task.task_no }}</strong>
              <span class="mv-badge" :class="taskTone(task.status)">{{ task.status }}</span>
            </header>
            <p class="mv-muted">
              {{ task.mode === 'confirmed' ? '已确认缺值' : '待核对' }} · 范围
              {{ task.scope_count }} 条 · 提交于 {{ task.submitted_at }}
            </p>
            <p class="mv-muted">
              任务范围：{{ task.scope.join('、') || '—' }}（重试按原提交范围）
            </p>
            <div class="mv-task-actions">
              <button
                class="link"
                type="button"
                :data-testid="`mv-task-open-${task.id}`"
                @click="openTask(task.id)"
              >
                查看详情
              </button>
              <button
                v-if="['待执行', '中断', '部分完成'].includes(task.status)"
                class="link"
                type="button"
                :data-testid="`mv-task-run-${task.id}`"
                @click="runTask(task.id)"
              >
                {{ task.status === '待执行' ? '执行补采' : '继续补采' }}
              </button>
            </div>
          </article>
          <p v-if="!tasks.length" class="empty-state">暂无补采任务</p>
        </section>
      </aside>
    </div>

    <div v-if="activeTask" class="modal-backdrop" data-testid="mv-task-modal">
      <div class="modal mv-detail">
        <header class="mv-detail-head">
          <div>
            <h3>补采详情 {{ activeTask.task_no }}</h3>
            <p class="mv-muted">
              {{ activeTask.status }} · {{ activeTask.quality_state }}范围 · 提交人
              {{ activeTask.submitted_by }} · 已重试 {{ activeTask.attempts }} 次
            </p>
          </div>
          <button class="btn ghost" type="button" data-testid="mv-task-close" @click="closeTask">
            关闭
          </button>
        </header>

        <p v-if="detailError" class="error-text" data-testid="mv-detail-error">{{ detailError }}</p>
        <p v-if="detailNotice" class="mv-notice" data-testid="mv-detail-notice">{{ detailNotice }}</p>

        <section class="mv-scope-box">
          <strong>提交时的范围快照</strong>
          <span class="mv-muted">
            {{ activeTask.scope.length }} 条原始行（{{ activeTask.scope.join('、') }}）；
            中断后重试仍按此范围，已补采的行不会再次入队。
          </span>
        </section>

        <table class="data-table">
          <thead>
            <tr>
              <th>原始行</th>
              <th>历史质量结论</th>
              <th>缺值字段</th>
              <th>补采状态</th>
              <th>核对 / 处置</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in activeTask.items"
              :key="item.raw_record_id"
              :data-testid="`mv-item-${item.raw_record_id}`"
            >
              <td>
                <strong>#{{ item.raw_record_id }}</strong>
                <small class="mv-body">{{ item.raw_body || '—' }}</small>
                <small class="mv-muted">行 {{ item.line_no }} · 版本 {{ item.parser_version }}</small>
              </td>
              <td>
                <span class="mv-badge" :class="item.conclusion_status === '已确认' ? 'ok' : 'warn'">
                  {{ item.conclusion_status }}
                </span>
                <small>#{{ item.quality_conclusion_id }} {{ item.conclusion_type }}</small>
                <small class="mv-muted">{{ item.conclusion_summary }}</small>
                <small v-if="item.historical_parser_version" class="mv-muted">
                  历史版本口径：{{ item.historical_parser_version }}
                </small>
              </td>
              <td>
                <span
                  v-for="entry in item.field_missing"
                  :key="entry.field"
                  class="mv-missing"
                  :class="entry.state"
                >
                  {{ entry.field }}
                </span>
                <span v-if="!item.field_missing.length" class="mv-muted">无</span>
              </td>
              <td>
                <span class="mv-badge" :class="itemTone(item.status)">{{ item.status }}</span>
                <small v-if="item.recovered_fields && Object.keys(item.recovered_fields).length">
                  补到：{{ formatFields(item.recovered_fields) }}
                </small>
                <small v-if="item.reparsed_entity_id">
                  已重新解析 → 明细 #{{ item.reparsed_entity_id }}
                </small>
                <small v-if="item.verified === false" class="mv-muted">尚未核实</small>
              </td>
              <td class="mv-item-actions">
                <template v-if="item.status === '待核对'">
                  <select
                    v-model="reviewChoices[item.raw_record_id]"
                    :data-testid="`mv-review-${item.raw_record_id}`"
                  >
                    <option value="new_format">实为新版格式</option>
                    <option value="old_format">实为旧版格式</option>
                    <option value="cannot_confirm">仍无法确认</option>
                  </select>
                  <button
                    class="link"
                    type="button"
                    :data-testid="`mv-review-submit-${item.raw_record_id}`"
                    @click="submitReview(item.raw_record_id)"
                  >
                    回写核对结论
                  </button>
                </template>
                <span v-else class="mv-muted">{{ item.review_conclusion || '—' }}</span>
                <small v-if="item.disposition_id" class="mv-muted">
                  处置清单 #{{ item.disposition_id }}
                </small>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="mv-detail-actions">
          <button
            v-if="['待执行', '中断', '部分完成'].includes(activeTask.status)"
            class="btn"
            type="button"
            data-testid="mv-detail-retry"
            @click="retryActive"
          >
            {{ activeTask.status === '待执行' ? '执行补采' : '按原范围重试' }}
          </button>
          <button
            v-if="canReparse"
            class="btn"
            type="button"
            data-testid="mv-detail-reparse"
            @click="reparseActive"
          >
            重新解析已补采原文
          </button>
          <button
            class="btn"
            type="button"
            data-testid="mv-detail-disposition"
            @click="writeActiveDisposition"
          >
            结论回写处置清单
          </button>
        </div>

        <section v-if="activeTask.dispositions?.length" class="mv-dispositions">
          <h4>处置清单</h4>
          <table class="data-table">
            <thead>
              <tr><th>原始行</th><th>字段结论</th><th>处置结果</th><th>下游可用</th></tr>
            </thead>
            <tbody>
              <tr v-for="entry in activeTask.dispositions" :key="entry.id">
                <td>#{{ entry.raw_record_id }}</td>
                <td>{{ entry.field_conclusions.map((c) => `${c.field}=${c.value || '空'}(${c.state})`).join('；') }}</td>
                <td>{{ entry.decision }}</td>
                <td>{{ entry.downstream_scope }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>

    <footer class="page-foot">
      <span>
        缺值记录 {{ view.total }} 条 · 已确认 {{ view.buckets['已确认缺值'] || 0 }} ·
        待核对 {{ view.buckets['待核对'] || 0 }}
      </span>
      <span class="mv-muted">视图生成于 {{ view.generated_at || '—' }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  listCollectTasks,
  runCollectTask,
  retryCollectTask,
  submitRecollect,
  verifyCollectItems,
  writeDisposition,
  reparseCollected,
  getCollectTask,
  loadMissingValueView,
} from '@/api/missing-value'
import type {
  MissingValueRow,
  MissingValueView,
  RecollectTask,
} from '@/data/types'

const emptyView: MissingValueView = {
  generated_at: '',
  bucket: '',
  buckets: {},
  field_stats: [],
  groups: [],
  rows: [],
  total: 0,
}

const view = ref<MissingValueView>(emptyView)
const tasks = ref<RecollectTask[]>([])
const filters = reactive({ batch_no: 'INGE-1001', source_id: '', parser_version: '' })
const activeBucket = ref('')
const selected = ref<number[]>([])
const errorMessage = ref('')
const notice = ref('')
const submitting = ref(false)
const activeTask = ref<RecollectTask | null>(null)
const detailError = ref('')
const detailNotice = ref('')
const reviewChoices = reactive<Record<number, 'new_format' | 'old_format' | 'cannot_confirm'>>({})

const bucketCards = computed(() => [
  { key: '', label: '全部缺值行', count: view.value.total, hint: '点击查看全部' },
  {
    key: '已确认缺值',
    label: '已确认缺值',
    count: view.value.buckets['已确认缺值'] || 0,
    hint: '可直接补采',
  },
  {
    key: '待核对',
    label: '待核对缺值',
    count: view.value.buckets['待核对'] || 0,
    hint: '核实后才能补采',
  },
  {
    key: '已补采',
    label: '已补采',
    count: view.value.buckets['已补采'] || 0,
    hint: '不会再次入队',
  },
  {
    key: '无需补采',
    label: '无需补采',
    count: view.value.buckets['无需补采'] || 0,
    hint: '按历史口径无缺值',
  },
])

const canReparse = computed(
  () => !!activeTask.value?.items.some((item) => item.status === '已补采' && !item.reparsed_entity_id),
)

function bucketClass(bucket: string) {
  return (
    {
      已确认缺值: 'confirmed',
      待核对: 'pending',
      已补采: 'recovered',
      无需补采: 'ok',
    }[bucket] || 'plain'
  )
}

function taskTone(status: string) {
  return (
    {
      已完成: 'ok',
      部分完成: 'warn',
      中断: 'warn',
      补采中: 'info',
      待执行: 'info',
      待核对: 'warn',
    }[status] || 'plain'
  )
}

function itemTone(status: string) {
  return (
    {
      已补采: 'ok',
      无需补采: 'ok',
      待核对: 'warn',
      待补采: 'info',
      失败: 'danger',
    }[status] || 'plain'
  )
}

function formatFields(fields: Record<string, string>) {
  return Object.entries(fields)
    .map(([key, value]) => `${key}=${value}`)
    .join('，')
}

function selectableRows(rows: MissingValueRow[]) {
  return rows.filter((row) => row.selectable)
}

function allSelected(rows: MissingValueRow[]) {
  const usable = selectableRows(rows)
  return usable.length > 0 && usable.every((row) => selected.value.includes(row.raw_record_id))
}

function toggleVersion(rows: MissingValueRow[], checked: boolean) {
  const usable = selectableRows(rows).map((row) => row.raw_record_id)
  if (checked) {
    selected.value = Array.from(new Set([...selected.value, ...usable]))
  } else {
    selected.value = selected.value.filter((id) => !usable.includes(id))
  }
}

function queryParams() {
  const params: Record<string, string> = {}
  if (filters.batch_no.trim()) params.batch_no = filters.batch_no.trim()
  if (filters.source_id.trim()) params.source_id = filters.source_id.trim()
  if (filters.parser_version.trim()) params.parser_version = filters.parser_version.trim()
  if (activeBucket.value) params.bucket = activeBucket.value
  return params
}

async function reloadView() {
  errorMessage.value = ''
  try {
    view.value = await loadMissingValueView(queryParams())
    const visible = new Set(view.value.rows.map((row) => row.raw_record_id))
    selected.value = selected.value.filter((id) => visible.has(id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺值视图读取失败'
  }
}

async function reloadTasks() {
  try {
    const payload = await listCollectTasks()
    tasks.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补采任务读取失败'
  }
}

async function reloadAll() {
  await Promise.all([reloadView(), reloadTasks()])
}

function switchBucket(key: string) {
  activeBucket.value = key
  selected.value = []
  void reloadView()
}

function applyFilters() {
  selected.value = []
  void reloadView()
}

function resetFilters() {
  filters.batch_no = ''
  filters.source_id = ''
  filters.parser_version = ''
  activeBucket.value = ''
  selected.value = []
  void reloadView()
}

async function submitScope(scope: 'confirmed' | 'pending') {
  if (!selected.value.length) {
    errorMessage.value = '请先在缺值视图中勾选原始行'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await submitRecollect({
      record_ids: [...selected.value],
      scope,
      batch_no: filters.batch_no.trim() || undefined,
      source_id: filters.source_id.trim() || undefined,
      parser_version: filters.parser_version.trim() || undefined,
      operator: '值班员',
    })
    if (!result.ok) {
      const detail = (result.rejected || [])
        .map((item) => `行 ${item.raw_record_id}：${item.reason}`)
        .join('；')
      throw new Error(detail ? `${result.message}（${detail}）` : result.message)
    }
    notice.value = result.message
    selected.value = []
    await reloadAll()
    if (result.task) activeTask.value = result.task
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补采提交失败'
  } finally {
    submitting.value = false
  }
}

async function openTask(taskId: number) {
  detailError.value = ''
  detailNotice.value = ''
  reviewChoicesReset()
  try {
    activeTask.value = await getCollectTask(taskId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补采详情读取失败'
  }
}

function closeTask() {
  activeTask.value = null
}

function reviewChoicesReset() {
  Object.keys(reviewChoices).forEach((key) => delete reviewChoices[Number(key)])
}

async function refreshActive(taskId: number) {
  activeTask.value = await getCollectTask(taskId)
  await reloadAll()
}

async function runTask(taskId: number) {
  detailError.value = ''
  detailNotice.value = ''
  try {
    const result = await runCollectTask(taskId)
    if (!result.ok) throw new Error(result.message)
    detailNotice.value = result.message
    notice.value = result.message
    await refreshActive(taskId)
  } catch (error) {
    const message = error instanceof Error ? error.message : '补采执行失败'
    if (activeTask.value) detailError.value = message
    else errorMessage.value = message
  }
}

async function retryActive() {
  if (!activeTask.value) return
  const taskId = activeTask.value.id
  detailError.value = ''
  detailNotice.value = ''
  try {
    const result = await retryCollectTask(taskId)
    if (!result.ok) throw new Error(result.message)
    detailNotice.value = result.message
    await refreshActive(taskId)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '补采重试失败'
  }
}

async function submitReview(rawId: number) {
  if (!activeTask.value) return
  const decision = reviewChoices[rawId] || 'cannot_confirm'
  detailError.value = ''
  detailNotice.value = ''
  try {
    const result = await verifyCollectItems(activeTask.value.id, [
      { raw_record_id: rawId, decision, operator: '值班员' },
    ])
    if (!result.ok) {
      const reason = (result.rejected || []).map((item) => item.reason).join('；')
      throw new Error(reason || result.message)
    }
    detailNotice.value = result.message
    await refreshActive(activeTask.value.id)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '核对结论回写失败'
  }
}

async function reparseActive() {
  if (!activeTask.value) return
  const taskId = activeTask.value.id
  detailError.value = ''
  detailNotice.value = ''
  try {
    const result = await reparseCollected(taskId)
    detailNotice.value = result.created_entity_ids?.length
      ? `${result.message}（明细 #${result.created_entity_ids.join('、')}）`
      : result.message
    await refreshActive(taskId)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '重新解析失败'
  }
}

async function writeActiveDisposition() {
  if (!activeTask.value) return
  const taskId = activeTask.value.id
  detailError.value = ''
  detailNotice.value = ''
  try {
    const result = await writeDisposition(taskId)
    detailNotice.value = result.skipped_raw_ids?.length
      ? `${result.message}；行 ${result.skipped_raw_ids.join('、')} 尚无可回写结论`
      : result.message
    await refreshActive(taskId)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '处置清单回写失败'
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.mv-layout { display: flex; gap: 16px; align-items: flex-start; }
.mv-main { flex: 1; min-width: 0; }
.mv-side { width: 340px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px; }
.mv-stat { cursor: pointer; }
.mv-stat.active { border-color: var(--brand); box-shadow: 0 0 0 2px rgb(31 111 235 / 15%); }
.mv-hint { display: block; color: var(--muted); font-size: 11px; }
.mv-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 12px; }
.mv-field span { display: block; font-size: 12px; color: var(--muted); }
.mv-field input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.mv-group { background: #fff; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 14px; }
.mv-group-head { display: flex; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.mv-version { padding: 10px 0 4px; }
.mv-version-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 0 14px 8px; }
.mv-version-name { font-family: monospace; font-size: 13px; }
.mv-rule-note { flex: 1 1 100%; font-size: 12px; }
.mv-muted { color: var(--muted); font-size: 12px; }
.mv-tag { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: #eef2f7; color: #334155; }
.mv-tag.ok { background: #e8f6ee; color: #15803d; }
.mv-tag.warn { background: #fdf1dc; color: #b45309; }
.mv-tag.fmt-new { background: #e6efff; color: #1d4ed8; }
.mv-tag.fmt-old { background: #f2ecff; color: #6d28d9; }
.mv-tag.fmt-unknown { background: #fdecec; color: #b42318; }
.mv-table th, .mv-table td { vertical-align: top; }
.mv-check { width: 32px; text-align: center; }
.mv-body { display: block; font-family: monospace; font-size: 12px; }
.mv-scope { font-size: 12px; }
.mv-scope span { display: block; }
.mv-effective { color: #1d4ed8; }
.mv-missing { display: inline-block; margin: 1px 4px 1px 0; padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.mv-missing.confirmed { background: #fde2e1; color: #b42318; }
.mv-missing.candidate { background: #fdf1dc; color: #b45309; }
.mv-badge { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 11px; background: #eef2f7; color: #334155; }
.mv-badge.confirmed { background: #fde2e1; color: #b42318; }
.mv-badge.pending { background: #fdf1dc; color: #b45309; }
.mv-badge.recovered, .mv-badge.ok { background: #e8f6ee; color: #15803d; }
.mv-badge.warn { background: #fdf1dc; color: #b45309; }
.mv-badge.info { background: #e6efff; color: #1d4ed8; }
.mv-badge.danger { background: #fde2e1; color: #b42318; }
.mv-blocked { display: block; font-size: 11px; color: var(--muted); margin-top: 2px; }
.mv-conclusion small { display: block; font-size: 11px; margin-top: 2px; }
.mv-row.bucket-pending { background: #fffaf1; }
.mv-row.bucket-recovered { background: #f6fbf8; }
.mv-submit { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-top: 12px; }
.mv-submit-head { display: flex; justify-content: space-between; margin-bottom: 8px; }
.mv-submit-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.mv-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.mv-panel h3 { margin: 0 0 8px; font-size: 14px; }
.mv-task { border-top: 1px solid #f0f2f5; padding: 8px 0; }
.mv-task:first-of-type { border-top: none; }
.mv-task-head { display: flex; justify-content: space-between; align-items: center; }
.mv-task-actions { display: flex; gap: 10px; margin-top: 4px; }
.mv-notice { background: #e8f6ee; color: #15803d; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.mv-empty { padding: 20px; }
.mv-detail { width: min(1080px, 100%); }
.mv-detail-head { display: flex; justify-content: space-between; align-items: flex-start; }
.mv-detail-head h3 { margin: 0 0 4px; }
.mv-detail-actions { display: flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.mv-scope-box { display: flex; flex-direction: column; gap: 2px; background: #f8fafc; border: 1px dashed var(--border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; font-size: 13px; }
.mv-item-actions { display: flex; flex-direction: column; gap: 4px; }
.mv-item-actions select { padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }
.mv-dispositions { margin-top: 12px; }
.mv-dispositions h4 { margin: 0 0 6px; }
</style>
