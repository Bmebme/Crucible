<template>
  <el-card shadow="never">
    <template #header>
      MCP 调用监控
      <span class="hint">Agent 每次调用 MCP 工具的记录（耗时 / 状态 / 超时 / 结果摘要）· 5 秒自动刷新</span>
      <el-button size="small" style="float: right" @click="load">刷新</el-button>
    </template>

    <div class="stats">
      <el-tag>近 {{ items.length }} 次调用</el-tag>
      <el-tag type="danger">超时 {{ nTimeout }}</el-tag>
      <el-tag type="warning">错误 {{ nError }}</el-tag>
      <el-tag type="info">平均耗时 {{ avgSec }}s</el-tag>
    </div>

    <el-table :data="items" size="small" max-height="calc(100vh - 300px)" v-loading="loading">
      <el-table-column prop="ts" label="时间" width="165" />
      <el-table-column label="工具" width="185">
        <template #default="{ row }">
          <el-tag size="small" :type="row.tool === 'kb_query' ? 'primary' : 'info'">{{ row.tool }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="project_id" label="项目" width="105" />
      <el-table-column label="耗时" width="95" sortable :sort-by="(r: any) => r.duration_ms">
        <template #default="{ row }">
          <span :class="{ slow: row.duration_ms >= 60000 }">{{ (row.duration_ms / 1000).toFixed(1) }}s</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="95">
        <template #default="{ row }">
          <el-tag size="small"
            :type="row.status === 'ok' ? 'success' : row.status === 'timeout' ? 'danger' : 'warning'">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="timeout" label="timeout" width="85" />
      <el-table-column label="摘要" min-width="300">
        <template #default="{ row }">
          <span v-if="row.brief?.timed_out" class="warn">到等待上限(返回已完成部分) </span>
          <span v-if="row.brief?.results != null">{{ row.brief.results }} 块结果 </span>
          <span v-if="row.brief?.timings" class="timings">{{ fmtTimings(row.brief.timings) }}</span>
          <span v-if="row.brief?.error" class="err">{{ row.brief.error }}</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!items.length && !loading" description="暂无 MCP 调用记录（Agent 调用工具后这里会出现）" />
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const items = ref<any[]>([])
const loading = ref(false)
let timer: any = null

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/fusion/mcp-calls', { params: { limit: 100 } })
    items.value = data.items ?? []
  } catch { /* 日志不可用时不打扰 */ } finally {
    loading.value = false
  }
}

const nTimeout = computed(() => items.value.filter((i) => i.status === 'timeout').length)
const nError = computed(() => items.value.filter((i) => i.status === 'error').length)
const avgSec = computed(() => {
  if (!items.value.length) return '0.0'
  const ms = items.value.reduce((s, i) => s + (i.duration_ms || 0), 0) / items.value.length
  return (ms / 1000).toFixed(1)
})

function fmtTimings(t: Record<string, number>) {
  return Object.entries(t).map(([k, v]) => `${k} ${v}s`).join(' · ')
}

onMounted(() => {
  load()
  timer = setInterval(load, 5000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.hint { font-size: 12px; color: #909399; margin-left: 12px; }
.stats { display: flex; gap: 8px; margin-bottom: 10px; }
.slow { color: #f56c6c; font-weight: 600; }
.warn { color: #e6a23c; }
.err { color: #f56c6c; }
.timings { color: #909399; font-size: 12px; }
</style>
