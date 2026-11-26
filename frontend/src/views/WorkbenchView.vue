<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button
          v-if="meta.key === 'extract_tmpl'"
          class="btn"
          data-testid="open-adoption"
          type="button"
          @click="openAdoptionFromTemplate"
        >
          按渠道申请采用
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="canReview && reviewAdoptionNo(row)"
              class="link"
              type="button"
              :data-testid="`review-${row.id}`"
              @click="openReview(row)"
            >
              差异复核
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <template v-if="showAdoptionForm">
          <p class="modal-hint">
            选择要借用的合同模板与批次，服务端核对有效授权与批次范围后生成渠道采用单，
            可在模板差异页发起复核。
          </p>
          <label class="create-field">
            <span>采用渠道</span>
            <select v-model="createValues['采用渠道']" data-testid="create-采用渠道" required>
              <option value="" disabled>请选择渠道</option>
              <option v-for="channel in adoptionForm.channels" :key="channel" :value="channel">
                {{ channel }}
              </option>
            </select>
          </label>
          <label class="create-field">
            <span>模板编号</span>
            <select v-model="createValues['模板编号']" data-testid="create-模板编号" required>
              <option value="" disabled>请选择派生模板</option>
              <option v-for="item in adoptionForm.templates" :key="item.模板编号 + item.版本号" :value="item.模板编号">
                {{ item.模板编号 }} {{ item.版本号 }} · 甲 · {{ item.金额表达式 }}
              </option>
            </select>
          </label>
          <label class="create-field">
            <span>关联批次</span>
            <select v-model="createValues['关联批次']" data-testid="create-关联批次" required>
              <option value="" disabled>请选择批次</option>
              <option v-for="item in adoptionForm.batches" :key="item.批次编号" :value="item.批次编号">
                {{ item.批次编号 }} · {{ item.来源渠道 }}
              </option>
            </select>
          </label>
          <label class="create-field">
            <span>申请人</span>
            <input v-model="createValues['申请人']" data-testid="create-申请人" placeholder="如 乙 / reviewer-02" />
          </label>
        </template>
        <template v-else>
          <label v-for="field in meta.fields" :key="field" class="create-field">
            <span>{{ field }}</span>
            <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
          </label>
        </template>
        <p v-if="formMessage" class="form-message" :class="{ error: formMessageIsError }">
          {{ formMessage }}
        </p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <section v-if="reviewPanel" class="review-panel" data-testid="adoption-review">
      <header class="review-head">
        <h3>模板差异复核 · 采用单 {{ reviewPanel.采用单号 }}</h3>
        <button class="btn ghost" type="button" @click="closeReview">收起</button>
      </header>
      <dl class="dag-detail">
        <dt>采用渠道</dt><dd>{{ reviewPanel.采用渠道 }}</dd>
        <dt>关联批次</dt><dd>{{ reviewPanel.关联批次 }}</dd>
        <dt>派生版本</dt><dd>{{ reviewPanel.模板编号 }} {{ reviewPanel.派生版本 }}</dd>
        <dt>金额表达式</dt><dd>{{ reviewPanel.金额表达式 }}</dd>
        <dt>差异编号</dt><dd>{{ reviewPanel.差异编号 }}（{{ reviewPanel.旧模板版本 }}→{{ reviewPanel.新模板版本 }}）</dd>
        <dt>受影响渠道</dt><dd>{{ reviewPanel.受影响渠道.join('、') || '—' }}</dd>
        <dt>差异来源批次</dt><dd>{{ reviewPanel.来源批次.join('、') || '—' }}</dd>
        <dt>同一采用单</dt><dd>{{ reviewPanel.关联采用单.join('、') || '—' }}</dd>
        <template v-if="resumeGuide">
          <dt>服务端下一步</dt><dd>{{ resumeGuide.下一步 }}</dd>
          <dt>批次可用表达式</dt>
          <dd>
            {{ resumeGuide.允许继续 ? `${resumeGuide.模板编号} ${resumeGuide.派生版本} · ${resumeGuide.金额表达式}` : '无可用授权，须停止解析' }}
            <span v-if="resumeGuide.撤回后沿用" class="tag-note">（撤回前已领取，沿用原版本）</span>
          </dd>
        </template>
        <template v-if="revocationReceipt">
          <dt>撤回回执</dt><dd>{{ revocationReceipt.撤回回执 }}</dd>
          <dt>下一批</dt>
          <dd>{{ revocationReceipt.下一批 || '—' }} · {{ revocationReceipt.下一批模板编号 || '—' }} {{ revocationReceipt.下一批派生版本 || '' }}</dd>
          <dt>下一批表达式</dt><dd>{{ revocationReceipt.下一批金额表达式 || '—' }}</dd>
        </template>
      </dl>
      <div v-if="scopePanel" class="scope-grid" data-testid="adoption-scope">
        <div>
          <h4>已授权</h4>
          <p v-for="item in scopePanel.已授权" :key="String(item.授权编号)">
            {{ item.授权编号 }} · {{ item.模板编号 }} {{ item.派生版本 }}
          </p>
          <p v-if="!scopePanel.已授权.length" class="muted">—</p>
        </div>
        <div>
          <h4>已领取</h4>
          <p v-for="(item, index) in scopePanel.已领取" :key="`c-${index}`">
            {{ item.模板编号 }} {{ item.派生版本 }} · {{ item.关联批次 }}
          </p>
          <p v-if="!scopePanel.已领取.length" class="muted">—</p>
        </div>
        <div>
          <h4>撤回后不可再用</h4>
          <p v-for="(item, index) in scopePanel.撤回后不可再用" :key="`r-${index}`">
            {{ item.模板编号 }} {{ item.派生版本 }} · {{ item.关联批次 }}
          </p>
          <p v-if="!scopePanel.撤回后不可再用.length" class="muted">—</p>
        </div>
      </div>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listEntries,
  loadAdoptionForm,
  loadAdoptionResume,
  loadAdoptionReview,
  loadAdoptionScope,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type {
  AdoptionForm,
  AdoptionReview,
  AdoptionScope,
  ResumeGuidance,
  EntryRow,
} from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const formMessage = ref('')
const formMessageIsError = ref(false)
const adoptionForm = ref<AdoptionForm>({ templates: [], channels: [], batches: [], grants: [] })
const reviewPanel = ref<AdoptionReview | null>(null)
const resumeGuide = ref<ResumeGuidance | null>(null)
const scopePanel = ref<AdoptionScope['范围'] | null>(null)
const revocationReceipt = ref<Record<string, unknown> | null>(null)
const isAdoption = computed(() => meta.key === 'tmpl_adopt')
const adoptionPanel = ref(false)
const showAdoptionForm = computed(() => isAdoption.value || adoptionPanel.value)
const canReview = computed(() => meta.key === 'tmpl_adopt' || meta.key === 'tmpl_diff')

/** 差异页按「关联采用单」复核；采用页按本行采用单号复核。 */
function reviewAdoptionNo(row: EntryRow): string {
  if (meta.key === 'tmpl_diff') {
    const first = String(row['关联采用单'] ?? '').split(',')[0]?.trim() ?? ''
    return first
  }
  return String(row['采用单号'] ?? '')
}
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  formMessage.value = ''
  formMessageIsError.value = false
  errorMessage.value = ''
  showCreate.value = true
  if (isAdoption.value) void loadFormOptions()
}

/** 抽取模板页：直接打开按渠道采用的表单（生成渠道采用单，供模板差异页复核）。 */
function openAdoptionFromTemplate() {
  createValues.value = {
    采用渠道: '',
    模板编号: '',
    关联批次: '',
    申请人: '',
  }
  formMessage.value = ''
  formMessageIsError.value = false
  errorMessage.value = ''
  adoptionPanel.value = true
  showCreate.value = true
  void loadFormOptions()
}

async function loadFormOptions() {
  try {
    adoptionForm.value = await loadAdoptionForm()
  } catch (error) {
    formMessage.value = error instanceof Error ? error.message : '采用表单选项读取失败'
    formMessageIsError.value = true
  }
}

async function submitCreate() {
  creating.value = true
  formMessage.value = ''
  formMessageIsError.value = false
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) {
      formMessage.value = result.message
      formMessageIsError.value = true
      return
    }
    showCreate.value = false
    formMessage.value = result.message
    await reload()
  } catch (error) {
    formMessage.value = error instanceof Error ? error.message : '登记失败'
    formMessageIsError.value = true
  } finally {
    creating.value = false
  }
}

function openReview(row: EntryRow) {
  const adoptionNo = reviewAdoptionNo(row)
  errorMessage.value = ''
  if (!adoptionNo) {
    errorMessage.value = '该行没有采用单号，无法按采用单复核'
    return
  }
  void reviewAdoption(adoptionNo)
}

async function reviewAdoption(adoptionNo: string) {
  try {
    reviewPanel.value = await loadAdoptionReview(adoptionNo)
    if (!reviewPanel.value.ok) {
      errorMessage.value = '采用单不存在或已归档'
      reviewPanel.value = null
      resumeGuide.value = null
      return
    }
    const resume = await loadAdoptionResume(adoptionNo)
    resumeGuide.value = resume.在途或下一批
    revocationReceipt.value = Object.keys(resume.撤回回执).length ? resume.撤回回执 : null
    const scope = await loadAdoptionScope(adoptionNo)
    scopePanel.value = scope.范围
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核信息读取失败'
  }
}

function closeReview() {
  reviewPanel.value = null
  resumeGuide.value = null
  scopePanel.value = null
  revocationReceipt.value = null
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
