<template>
  <section class="page" data-module="data_mart">
    <header class="page-head">
      <div>
        <h2>数据集市管理</h2>
        <p class="page-desc">维护集市表，围绕表编号、表名称、主题域、事实粒度做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记集市表</button>
        <button class="btn" type="button" @click="exportRows">导出数据集市清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据集市数据，可先登记集市表</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">地区收入汇总</h3>
    <p class="page-desc">用维度归属映射给订单归属地区后计算地区收入；每次汇总固化一个映射版本，无法归属的订单单独计入待核对金额。</p>
    <form class="filter-bar" @submit.prevent="runSummary">
      <label class="filter-item">
        <span>业务日期</span>
        <input v-model="summaryForm.业务日期" placeholder="YYYY-MM-DD" required />
      </label>
      <label class="filter-item">
        <span>映射版本号（可选）</span>
        <input v-model="summaryForm.映射版本号" placeholder="缺省取当前生效版本" />
      </label>
      <button class="btn primary" type="submit">发起汇总</button>
      <span class="page-desc">指定历史版本可重现旧报表</span>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in summaryColumns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in summaryRuns" :key="String(run.id)" :class="{ 'row-abnormal': run.abnormal }">
          <td v-for="column in summaryColumns" :key="column">{{ run[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(run)">明细</button>
            <button class="link" type="button" @click="reviewRun(run)">复核</button>
            <button class="link" type="button" @click="openImpact(run)">影响对比</button>
          </td>
        </tr>
        <tr v-if="!summaryRuns.length">
          <td :colspan="summaryColumns.length + 1" class="empty-state">暂无地区收入汇总批次</td>
        </tr>
      </tbody>
    </table>

    <div v-if="impactResult" class="impact-panel">
      <h3 class="section-title">
        归属冲突影响：{{ impactResult.基准批次 }}（{{ impactResult.基准版本 }}） → {{ impactResult.对比批次 }}（{{ impactResult.对比版本 }}）
      </h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>地区编码</th>
            <th>地区名称</th>
            <th>基准收入</th>
            <th>对比收入</th>
            <th>差额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="diff in impactResult.地区差异" :key="diff.地区编码" :class="{ 'row-abnormal': diff.差额 !== 0 }">
            <td>{{ diff.地区编码 }}</td>
            <td>{{ diff.地区名称 }}</td>
            <td>{{ diff.基准收入 }}</td>
            <td>{{ diff.对比收入 }}</td>
            <td>{{ diff.差额 }}</td>
          </tr>
        </tbody>
      </table>
      <p class="page-desc">
        待核对金额差异：{{ impactResult.待核对差异 }}；关联冲突：
        <span v-if="impactResult.关联冲突.length">
          {{ impactResult.关联冲突.map((c: Row) => `${c.冲突编号}（成员 ${c.成员编码}，采用 ${c.采用上级}，影响金额 ${c.影响金额}）`).join('；') }}
        </span>
        <span v-else>无</span>
      </p>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据集市记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记集市表</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="detailRun" class="modal-overlay" @click.self="detailRun = null">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>汇总批次 {{ detailRun.汇总批次号 }}</h3>
            <button class="btn ghost" type="button" @click="detailRun = null">&times;</button>
          </header>
          <div class="modal-body">
            <p class="page-desc">
              业务日期 {{ detailRun.业务日期 }} · 映射版本 {{ detailRun.映射版本号 }} · 订单批次 {{ detailRun.订单批次号 }} · 状态 {{ detailRun.status }}
            </p>
            <table class="data-table">
              <thead>
                <tr>
                  <th>地区编码</th>
                  <th>地区名称</th>
                  <th>地区收入</th>
                  <th>订单笔数</th>
                  <th>映射版本号</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in detailRun.地区明细" :key="item.地区编码">
                  <td>{{ item.地区编码 }}</td>
                  <td>{{ item.地区名称 }}</td>
                  <td>{{ item.地区收入 }}</td>
                  <td>{{ item.订单笔数 }}</td>
                  <td>{{ item.映射版本号 }}</td>
                </tr>
              </tbody>
            </table>
            <p class="page-desc">
              输入订单总额 {{ detailRun.输入订单总额 }} = 地区收入合计 {{ detailRun.地区收入合计 }} + 待核对金额 {{ detailRun.待核对金额 }}（对账差额 {{ detailRun.对账差额 }}）
            </p>
            <p class="page-desc">待核对订单：{{ detailRun.待核对订单 || '无' }}（{{ detailRun.待核对笔数 }} 笔）</p>
            <p v-if="detailRun.复核结论" class="page-desc">复核结论：{{ detailRun.复核结论 }}</p>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="impactRun" class="modal-overlay" @click.self="impactRun = null">
        <div class="modal-card">
          <header class="modal-head">
            <h3>选择对比基准</h3>
            <button class="btn ghost" type="button" @click="impactRun = null">&times;</button>
          </header>
          <div class="modal-body">
            <p class="page-desc">对比批次：{{ impactRun.汇总批次号 }}（{{ impactRun.映射版本号 }}），请选择同一业务日的基准批次：</p>
            <label class="modal-field">
              <span>基准批次</span>
              <select v-model="impactBaseline">
                <option v-for="option in impactOptions" :key="option.id" :value="option.id">
                  {{ option.汇总批次号 }}（{{ option.映射版本号 }}）
                </option>
              </select>
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="impactRun = null">取消</button>
              <button class="btn primary" type="button" @click="loadImpact">查看影响</button>
            </footer>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/data_mart'
const columns = ["表编号", "表名称", "主题域", "事实粒度", "关联维度", "度量字段", "刷新周期", "表状态"]
const actions = ["提交建模", "发布集市", "下线集市"]
const statuses = ["草稿", "已建模", "已发布", "已下线"]
const stats = [{"label": "已发布集市", "value": 0}, {"label": "草稿集市", "value": 0}, {"label": "下线集市", "value": 0}]
const summaryColumns = ["汇总批次号", "业务日期", "映射版本号", "订单批次号", "输入订单总额", "地区收入合计", "待核对金额", "对账差额", "status"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

const summaryRuns = ref<Row[]>([])
const summaryForm = ref<Record<string, string>>({ 业务日期: '', 映射版本号: '' })
const detailRun = ref<Row | null>(null)
const impactRun = ref<Row | null>(null)
const impactBaseline = ref<number | null>(null)
const impactResult = ref<Row | null>(null)

const impactOptions = computed(() =>
  summaryRuns.value.filter((run) => run.id !== impactRun.value?.id && run.业务日期 === impactRun.value?.业务日期),
)

function updateStats() {
  const data = rows.value
  stats.length > 0 && (stats[0].value = data.filter((r: Row) => r.status === statuses[1] || r.pending).length)
  stats.length > 1 && (stats[1].value = data.filter((r: Row) => r.status && !r.pending && !r.abnormal).length)
  stats.length > 2 && (stats[2].value = data.filter((r: Row) => r.abnormal).length)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createFields.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || '登记失败')
    }
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('数据集市动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集市操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('集市表列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '集市表列表读取失败'
  }
}

async function reloadSummaryRuns() {
  try {
    const response = await request(`${ENDPOINT}/region_summary`)
    if (!response.ok) {
      throw new Error('地区收入汇总读取失败')
    }
    const payload = await response.json()
    summaryRuns.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '地区收入汇总读取失败'
  }
}

async function runSummary() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const values: Record<string, string> = { 业务日期: summaryForm.value.业务日期 }
    if (summaryForm.value.映射版本号) {
      values.映射版本号 = summaryForm.value.映射版本号
    }
    const response = await request(`${ENDPOINT}/region_summary`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '汇总失败')
    }
    noticeMessage.value = payload.message
    await reloadSummaryRuns()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇总失败'
  }
}

async function openDetail(run: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/region_summary/${run.id}`)
    if (!response.ok) {
      throw new Error('汇总明细读取失败')
    }
    detailRun.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇总明细读取失败'
  }
}

async function reviewRun(run: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/region_summary/${run.id}/review`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '复核失败')
    }
    noticeMessage.value = `${payload.message}：${payload.entry.复核结论}`
    await reloadSummaryRuns()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核失败'
  }
}

function openImpact(run: Row) {
  errorMessage.value = ''
  impactRun.value = run
  impactBaseline.value = null
  impactResult.value = null
}

async function loadImpact() {
  errorMessage.value = ''
  if (!impactRun.value || impactBaseline.value === null) {
    errorMessage.value = '请先选择基准批次'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/region_summary/${impactRun.value.id}/impact?baseline=${impactBaseline.value}`)
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '影响对比失败')
    }
    impactResult.value = payload.item
    noticeMessage.value = payload.message
    impactRun.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响对比失败'
  }
}

onMounted(() => {
  void reload()
  void reloadSummaryRuns()
})
</script>

<style scoped>
.section-title {
  font-size: 14px;
  margin: 16px 0 8px;
}
.impact-panel {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}
.notice-text {
  color: #067647;
}
.row-abnormal td {
  background: #fef3f2;
}
.modal-wide {
  width: 640px;
}
.modal-field select {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
</style>
