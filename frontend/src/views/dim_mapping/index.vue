<template>
  <section class="page" data-module="dim_mapping">
    <header class="page-head">
      <div>
        <h2>维度归属映射</h2>
        <p class="page-desc">接收维度版本区间及成员关系数据，按成员归并带起止日期的上下级关系；成员在区间交界当天最多归属一个上级。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openReceive">接收成员关系</button>
        <button class="btn" type="button" @click="mergeVersions">归并新版本</button>
        <button class="btn" type="button" @click="exportRows">导出映射清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">当前生效版本</span>
        <strong class="stat-value">{{ currentVersion || '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待归并关系</span>
        <strong class="stat-value">{{ pendingRelations }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">归属冲突</span>
        <strong class="stat-value">{{ conflicts.length }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="lookupAttribution">
      <label class="filter-item">
        <span>成员编码</span>
        <input v-model="lookupForm.member" placeholder="如 S03" required />
      </label>
      <label class="filter-item">
        <span>业务日期</span>
        <input v-model="lookupForm.date" placeholder="YYYY-MM-DD" required />
      </label>
      <label class="filter-item">
        <span>映射版本号（可选）</span>
        <input v-model="lookupForm.version" placeholder="缺省取当前生效版本" />
      </label>
      <button class="btn" type="submit">按业务日查归属</button>
      <span v-if="lookupResult" class="lookup-result" :class="{ 'error-text': !lookupOk }">{{ lookupResult }}</span>
    </form>

    <h3 class="section-title">映射版本</h3>
    <div class="version-row">
      <button
        v-for="ver in versions"
        :key="ver.版本号"
        class="btn version-chip"
        :class="{ primary: ver.版本号 === activeVersion }"
        type="button"
        @click="selectVersion(ver.版本号)"
      >
        {{ ver.版本号 }}（{{ ver.状态 }} · {{ ver.行数 }} 行）
      </button>
      <span v-if="!versions.length" class="empty-state">暂无映射版本，请先接收成员关系并归并</span>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in mappingColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in mappingRows" :key="String(row.id)">
          <td v-for="column in mappingColumns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!mappingRows.length">
          <td :colspan="mappingColumns.length" class="empty-state">该版本暂无映射行</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">成员关系接收表</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in relationColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in relations" :key="String(row.id)" :class="{ 'row-abnormal': row.abnormal }">
          <td v-for="column in relationColumns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!relations.length">
          <td :colspan="relationColumns.length" class="empty-state">暂无成员关系数据</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">归属冲突记录</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in conflictColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in conflicts" :key="String(row.id)" class="row-abnormal">
          <td v-for="column in conflictColumns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!conflicts.length">
          <td :colspan="conflictColumns.length" class="empty-state">暂无归属冲突</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ mappingTotal }} 条归属映射记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showReceiveModal" class="modal-overlay" @click.self="showReceiveModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>接收成员关系</h3>
            <button class="btn ghost" type="button" @click="showReceiveModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitReceive">
            <label v-for="field in receiveFields" :key="field.name" class="modal-field">
              <span>{{ field.label }}</span>
              <input v-model="receiveForm[field.name]" :placeholder="field.placeholder" :required="field.required" />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showReceiveModal = false">取消</button>
              <button class="btn primary" type="submit">提交接收</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/dim_mapping'
const mappingColumns = ['版本号', '成员编码', '成员名称', '上级编码', '上级名称', '起始日期', '截止日期', '来源批次', 'status']
const relationColumns = ['更正批次号', '成员编码', '成员名称', '上级编码', '上级名称', '生效日期', '失效日期', '接收序号', 'status', '归并版本号']
const conflictColumns = ['冲突编号', '成员编码', '冲突日期', '冲突区间', '更正批次甲', '上级甲', '更正批次乙', '上级乙', '采用上级', '涉及汇总批次', '影响金额', 'status']
const receiveFields = [
  { name: '更正批次号', label: '更正批次号', placeholder: '如 FIX-20260919-01', required: true },
  { name: '成员编码', label: '成员编码', placeholder: '如 S05', required: true },
  { name: '成员名称', label: '成员名称', placeholder: '可选', required: false },
  { name: '上级编码', label: '上级编码', placeholder: '如 R-SOUTH', required: true },
  { name: '上级名称', label: '上级名称', placeholder: '可选', required: false },
  { name: '生效日期', label: '生效日期', placeholder: 'YYYY-MM-DD', required: true },
  { name: '失效日期', label: '失效日期', placeholder: 'YYYY-MM-DD', required: true },
]

const versions = ref<Row[]>([])
const activeVersion = ref('')
const currentVersion = ref('')
const mappingRows = ref<Row[]>([])
const mappingTotal = ref(0)
const relations = ref<Row[]>([])
const pendingRelations = ref(0)
const conflicts = ref<Row[]>([])
const errorMessage = ref('')
const noticeMessage = ref('')
const lookupForm = ref<Record<string, string>>({ member: '', date: '', version: '' })
const lookupResult = ref('')
const lookupOk = ref(false)
const showReceiveModal = ref(false)
const receiveForm = ref<Record<string, string>>({})

async function fetchJson(path: string) {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}`)
  }
  return response.json()
}

async function reloadVersions(selectLatest = false) {
  const payload = await fetchJson(`${ENDPOINT}/versions`)
  versions.value = payload.items ?? []
  const active = versions.value.find((item) => item.状态 === '生效中')
  currentVersion.value = active ? active.版本号 : ''
  if (selectLatest && active) {
    activeVersion.value = active.版本号
  }
  if (!activeVersion.value && versions.value.length) {
    activeVersion.value = versions.value[versions.value.length - 1].版本号
  }
}

async function reloadMappings() {
  if (!activeVersion.value) {
    mappingRows.value = []
    mappingTotal.value = 0
    return
  }
  const query = new URLSearchParams({ 版本号: activeVersion.value, size: '200' }).toString()
  const payload = await fetchJson(`${ENDPOINT}?${query}`)
  mappingRows.value = payload.items ?? []
  mappingTotal.value = payload.total ?? mappingRows.value.length
}

async function reloadRelations() {
  const payload = await fetchJson(`${ENDPOINT}/relations`)
  relations.value = payload.items ?? []
  pendingRelations.value = relations.value.filter((row) => row.status === '待归并').length
}

async function reloadConflicts() {
  const payload = await fetchJson(`${ENDPOINT}/conflicts`)
  conflicts.value = payload.items ?? []
}

async function reloadAll(selectLatest = false) {
  errorMessage.value = ''
  try {
    await reloadVersions(selectLatest)
    await Promise.all([reloadMappings(), reloadRelations(), reloadConflicts()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维度归属映射数据读取失败'
  }
}

function selectVersion(version: string) {
  activeVersion.value = version
  void reloadMappings()
}

async function lookupAttribution() {
  errorMessage.value = ''
  noticeMessage.value = ''
  lookupResult.value = ''
  const query = new URLSearchParams({
    member: lookupForm.value.member,
    date: lookupForm.value.date,
  })
  if (lookupForm.value.version) {
    query.set('version', lookupForm.value.version)
  }
  try {
    const payload = await fetchJson(`${ENDPOINT}/lookup?${query.toString()}`)
    lookupOk.value = Boolean(payload.ok)
    lookupResult.value = payload.ok
      ? `${payload.item.成员编码} 在 ${payload.item.业务日期} 归属 ${payload.item.上级编码} ${payload.item.上级名称}（${payload.item.版本号}，${payload.item.起始日期}~${payload.item.截止日期}）`
      : payload.message
  } catch (error) {
    lookupOk.value = false
    lookupResult.value = error instanceof Error ? error.message : '归属查询失败'
  }
}

function openReceive() {
  errorMessage.value = ''
  receiveForm.value = {}
  showReceiveModal.value = true
}

async function submitReceive() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/relations`, {
      method: 'POST',
      body: JSON.stringify({ values: receiveForm.value }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '成员关系接收失败')
    }
    showReceiveModal.value = false
    noticeMessage.value = payload.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '成员关系接收失败'
  }
}

async function mergeVersions() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/merge`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '归并失败')
    }
    noticeMessage.value = payload.message
    await reloadAll(true)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归并失败'
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(() => reloadAll(true))
</script>

<style scoped>
.section-title {
  font-size: 14px;
  margin: 16px 0 8px;
}
.version-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.version-chip {
  font-size: 12px;
}
.lookup-result {
  font-size: 13px;
  align-self: center;
}
.notice-text {
  color: #067647;
}
.row-abnormal td {
  background: #fef3f2;
}
</style>
