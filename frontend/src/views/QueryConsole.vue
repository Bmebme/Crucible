<template>
  <div>
    <el-card shadow="never" class="query-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="查询类型">
          <el-radio-group v-model="mode">
            <el-radio-button value="query">融合查询</el-radio-button>
            <el-radio-button value="enum">枚举</el-radio-button>
            <el-radio-button value="experience">经验查询</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="项目">
          <el-select v-model="projectId" style="width: 140px">
            <el-option v-for="p in projects" :key="p.id" :label="p.id" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="对齐模式">
          <el-select v-model="aliasMode" style="width: 110px">
            <el-option label="l2+l3" value="l2+l3" />
            <el-option label="l3 (纯LLM)" value="l3" />
            <el-option label="off" value="off" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="mode === 'experience'" label="环境">
          <el-input v-model="env" placeholder="staging / production" style="width: 140px" />
        </el-form-item>
      </el-form>

      <el-input
        v-model="query"
        :placeholder="placeholder"
        size="large"
        clearable
        @keyup.enter="run"
      >
        <template #append>
          <el-button type="primary" :loading="loading" @click="run">查询</el-button>
        </template>
      </el-input>

      <div v-if="prediction" class="predict-row">
        <span class="hint">类型预判：</span>
        <el-tag size="large" :type="qtypeColor(prediction.query_type)" effect="dark">
          {{ prediction.query_type }} {{ typeLabel(prediction.query_type) }}
        </el-tag>
        <el-tag size="small" type="info">合并 {{ prediction.merge_mode }}</el-tag>
        <el-tag size="small" type="info" v-if="prediction.rewritten_to" class="predict-rewrite">
          追问已消解: {{ prediction.rewritten_to }}
        </el-tag>
        <span class="hint">
          {{ prediction.matched_by?.startsWith('rule')
            ? '命中规则: ' + prediction.matched_by.slice(5)
            : prediction.matched_by === 'llm' ? 'LLM 判定' : '保守兜底' }}
        </span>
      </div>

      <div class="history-row">
        <span class="hint">多轮历史（追问时用于指代消解，每行一条，最早在前）：</span>
        <el-input
          v-model="historyText"
          type="textarea" :rows="2"
          placeholder="MAE 有哪些外部接口？&#10;FM REST API 是做什么的？"
        />
      </div>
    </el-card>

    <el-card v-if="notes.length" shadow="never" class="notes-card">
      <template #header>路由与合并</template>
      <div class="notes">
        <el-tag v-for="n in notes" :key="n" size="small" class="note-tag"
          :type="n.includes('rewritten') ? 'warning' : n.includes('alias') ? 'success' : 'info'">
          {{ n }}
        </el-tag>
      </div>
    </el-card>

    <el-card v-if="result" shadow="never" class="result-card">
      <template #header>
        <span>结果</span>
        <el-tag v-if="result.routing" class="qtype" size="small"
          :type="qtypeColor(result.routing.query_type)">
          {{ result.routing.query_type }}
          <template v-if="result.routing.confidence != null">· {{ result.routing.confidence }}</template>
        </el-tag>
        <span class="count">{{ result.results?.length ?? 0 }} 项</span>
      </template>

      <!-- 枚举型 (路由 Q1, 无论从哪个模式入口): 导读 + 分组清单 -->
      <div v-if="result.routing?.query_type === 'Q1' && result.results?.length">
        <el-alert
          v-if="result.summary"
          type="success" :closable="false" class="enum-summary"
          title="导读"
          :description="result.summary"
        />
        <div class="enum-stats">
          <el-tag type="info">共 {{ result.results.length }} 项</el-tag>
          <el-tag type="warning">wiki {{ enumGroups.wikiCount }} 页</el-tag>
          <el-tag type="success">rag {{ enumGroups.ragCount }} 实体</el-tag>
          <span class="hint">清单为权威本体; wiki 条目可点击查看原文</span>
        </div>
        <el-collapse v-model="enumOpen" class="enum-collapse">
          <el-collapse-item v-for="(items, key) in enumGroups.groups" :key="key" :name="key">
            <template #title>
              <b>{{ enumGroupLabel(key) }}</b>
              <span class="enum-count">{{ items.length }}</span>
            </template>
            <div class="enum-items">
              <el-tooltip
                v-for="(r, i) in items" :key="i"
                :content="(r.snippet || r.description || '') + (r.entity_type ? ' [' + r.entity_type + ']' : '')"
                placement="top" :show-after="200"
                :disabled="!r.snippet && !r.description"
              >
                <el-link
                  type="primary" class="enum-tag" :underline="false"
                  @click="r.name.startsWith('wiki/') && openPage(r.name)"
                >
                  {{ r.name }}
                </el-link>
              </el-tooltip>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <div v-else-if="result.results?.length" class="results">
        <div v-for="(r, i) in result.results" :key="i" class="result-item">
          <div class="ri-head">
            <span class="ri-name">{{ r.title || r.name || r.conclusion?.slice(0, 60) || '结论' }}</span>
            <el-tag v-for="p in r.provenance || []" :key="p" size="small" type="info">{{ p }}</el-tag>
            <el-tag v-if="r.state" size="small" :type="stateColor(r.state)">{{ r.state }}</el-tag>
            <el-tag v-if="r.weight != null" size="small" type="warning">w={{ r.weight }}</el-tag>
            <el-tag v-if="r.confidence" size="small" type="success">{{ r.confidence }}</el-tag>
          </div>
          <div v-if="r.snippet" class="ri-snippet">{{ r.snippet }}</div>
          <div v-if="r.note" class="ri-note">📌 {{ r.note }}</div>
          <div v-if="r.path" class="ri-path">📄 {{ r.path }}</div>
          <div v-if="r.citations?.length" class="ri-citations">
            <div class="cit-title">🔗 引用（{{ r.citations.length }}）</div>
            <div v-for="(c, ci) in r.citations" :key="ci" class="cit-item">
              <el-tag size="small" :type="c.source === 'wiki' ? 'primary' : 'success'">{{ c.source }}</el-tag>
              <template v-if="c.source === 'wiki' && c.path">
                <el-link type="primary" class="cit-link" @click="openPage(c.path)">{{ c.path }}</el-link>
              </template>
              <span v-else-if="c.heading_path" class="cit-heading">{{ c.heading_path }}</span>
              <div v-if="c.excerpt" class="cit-excerpt">{{ c.excerpt.slice(0, 200) }}</div>
            </div>
          </div>
        </div>
      </div>
      <el-empty v-else description="无结果" />

      <template v-if="result.differences?.length">
        <el-divider content-position="left">
          <b>差异清单</b>（知识缺口信号）{{ result.differences.length }} 项
        </el-divider>
        <el-table :data="result.differences.slice(0, 50)" size="small" max-height="320">
          <el-table-column prop="only_in" label="缺失侧" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.only_in === 'rag' ? 'success' : 'warning'">{{ row.only_in }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="item" label="条目" min-width="260" show-overflow-tooltip />
          <el-table-column prop="action" label="建议" min-width="240" show-overflow-tooltip />
        </el-table>
      </template>

      <template v-if="result.conflicts?.length">
        <el-divider content-position="left"><b>冲突对峙</b>（不裁决，交由 Agent/人）</el-divider>
        <el-alert
          v-for="(c, i) in result.conflicts" :key="i"
          type="error" :closable="false" class="conflict"
          :title="c.wiki_says?.claim?.slice(0, 80) || 'wiki 侧'"
          :description="`rag 侧: ${c.rag_says?.claim?.slice(0, 120) || ''}`"
        />
      </template>
    </el-card>

    <el-drawer v-model="drawer" :title="pagePath" size="55%">
      <pre class="page-content">{{ pageContent || '加载中…' }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fusionEnum, fusionExperience, fusionQuery, listProjects } from '../api'

const mode = ref('query')
const projectId = ref('')
const projects = ref<Array<{ id: string }>>([])
const aliasMode = ref('l2+l3')
const env = ref('staging')
const query = ref('')
const historyText = ref('')
const loading = ref(false)
const result = ref<any>(null)
const notes = ref<string[]>([])
const drawer = ref(false)
const pagePath = ref('')
const pageContent = ref('')
const prediction = ref<any>(null)
let predictTimer: any = null
const enumOpen = ref<string[]>([])
const enumGroups = ref<{ groups: Record<string, any[]>; wikiCount: number; ragCount: number }>({
  groups: {}, wikiCount: 0, ragCount: 0,
})

function buildEnumGroups(results: any[]) {
  const groups: Record<string, any[]> = {}
  let wikiCount = 0, ragCount = 0
  for (const r of results) {
    const n = r.name || ''
    let key: string
    if (n.startsWith('wiki/')) {
      key = 'wiki:' + (n.slice(5).split('/')[0] || '其他')
      wikiCount++
    } else {
      key = 'rag:实体'
      ragCount++
    }
    ;(groups[key] ||= []).push(r)
  }
  enumGroups.value = { groups, wikiCount, ragCount }
  enumOpen.value = Object.keys(groups)
}

function enumGroupLabel(key: string) {
  const labels: Record<string, string> = {
    'wiki:concepts': '概念页', 'wiki:entities': '实体页', 'wiki:queries': '查询页',
    'wiki:sources': '源文档', 'wiki:verification': '验证记录', 'rag:实体': 'LightRAG 实体',
  }
  return labels[key] ?? key
}

function typeLabel(t: string) {
  return { Q1: '枚举型·全', Q2: '机制型·准', Q3: '经验型·可信' }[t] ?? ''
}

// 输入即预判 (600ms 防抖, 只分类不检索)
watch(query, (v) => {
  clearTimeout(predictTimer)
  const q = v.trim()
  if (!q) { prediction.value = null; return }
  predictTimer = setTimeout(async () => {
    try {
      const { data } = await api.post('/fusion/classify', {
        query: q, project_id: projectId.value,
        history: historyText.value.split('\n').map((s) => s.trim()).filter(Boolean),
      })
      prediction.value = data
    } catch { prediction.value = null }
  }, 600)
})

async function openPage(path: string) {
  pagePath.value = path
  pageContent.value = ''
  drawer.value = true
  try {
    const { data } = await api.get(`/projects/${projectId.value}/pages/content`, { params: { path } })
    pageContent.value = data.content
  } catch (e: any) {
    pageContent.value = '加载失败: ' + (e?.response?.data?.detail ?? e?.message)
  }
}

const placeholder = ref('例如: MAE 有哪些外部接口？')

onMounted(async () => {
  try {
    projects.value = await listProjects()
    projectId.value = projects.value[0]?.id ?? ''
  } catch (e: any) {
    ElMessage.warning('后端未连接: ' + (e?.message ?? e))
  }
})

function qtypeColor(t: string) {
  return { Q1: 'success', Q2: 'primary', Q3: 'warning' }[t] ?? 'info'
}
function stateColor(s: string) {
  return {
    verified_success: 'success', unverified: 'info',
    verified_blocked: 'danger', false_positive: 'warning',
  }[s] ?? 'info'
}

async function run() {
  if (!query.value.trim()) { ElMessage.info('输入查询内容'); return }
  loading.value = true
  result.value = null
  notes.value = []
  try {
    const history = historyText.value.split('\n').map((s) => s.trim()).filter(Boolean)
    let data: any
    if (mode.value === 'enum') data = await fusionEnum(query.value.trim(), projectId.value, aliasMode.value)
    else if (mode.value === 'experience') data = await fusionExperience(query.value.trim(), projectId.value, env.value)
    else data = await fusionQuery({ query: query.value.trim(), project_id: projectId.value, history, alias_mode: aliasMode.value })
    result.value = data
    notes.value = data.notes ?? []
    if (data.routing?.query_type === 'Q1') {
      buildEnumGroups(data.results ?? [])
      // 融合查询入口问出 Q1: 补一次导读 (文字化回答)
      if (!data.summary) {
        try {
          const e = await fusionEnum(query.value.trim(), projectId.value, aliasMode.value)
          result.value = { ...data, summary: e.summary ?? '' }
        } catch { /* 导读失败保留纯清单 */ }
      }
    }
    if (data.differences?.length) ElMessage.success(`结果 ${data.results?.length ?? 0} 项, 差异 ${data.differences.length} 项`)
  } catch (e: any) {
    ElMessage.error('查询失败: ' + (e?.response?.data?.detail ?? e?.message ?? e))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.query-card { margin-bottom: 16px; }
.predict-row { margin-top: 10px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.predict-rewrite { max-width: 420px; overflow: hidden; text-overflow: ellipsis; }
.history-row { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.hint { font-size: 12px; color: #909399; }
.notes { display: flex; flex-wrap: wrap; gap: 6px; }
.qtype { margin-left: 8px; }
.count { float: right; color: #909399; font-size: 13px; }
.result-item { padding: 10px 0; border-bottom: 1px dashed #e4e7ed; }
.ri-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ri-name { font-weight: 600; margin-right: 4px; }
.ri-snippet { margin-top: 6px; color: #303133; white-space: pre-wrap; font-size: 13px; line-height: 1.6; }
.ri-note { margin-top: 4px; color: #b8860b; font-size: 12px; }
.ri-path { margin-top: 4px; color: #909399; font-size: 12px; }
.ri-citations { margin-top: 8px; background: #f8fafc; border-radius: 6px; padding: 8px 10px; }
.cit-title { font-size: 12px; color: #606266; margin-bottom: 6px; }
.cit-item { margin-bottom: 6px; font-size: 12px; }
.cit-link { font-size: 12px; margin-left: 4px; }
.cit-heading { color: #606266; margin-left: 4px; }
.cit-excerpt {
  margin-top: 3px; color: #909399; font-size: 12px; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.page-content { white-space: pre-wrap; font-size: 13px; line-height: 1.7; }
.conflict { margin-bottom: 8px; }
</style>

<style scoped>
.enum-stats { display: flex; gap: 8px; align-items: center; margin: 10px 0; }
.enum-summary { margin-bottom: 6px; }
.enum-collapse { border: none; }
.enum-count { margin-left: 8px; color: #909399; font-size: 12px; }
.enum-items { display: flex; flex-wrap: wrap; gap: 8px 6px; padding: 4px 8px; }
.enum-tag { font-size: 12px; }
</style>
