<template>
  <el-card shadow="never">
    <template #header>
      知识台账
      <el-select v-model="projectId" style="width: 140px; margin-left: 12px" @change="refresh">
        <el-option v-for="p in projects" :key="p.id" :label="p.id" :value="p.id" />
      </el-select>
    </template>

    <el-tabs v-model="tab" @tab-change="refresh">
      <el-tab-pane label="差异清单 (M1)" name="diffs">
        <el-table :data="diffs" size="small" max-height="520">
          <el-table-column prop="id" label="#" width="60" />
          <el-table-column prop="only_in" label="缺失侧" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.only_in === 'rag' ? 'success' : 'warning'">{{ row.only_in }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="item" label="条目" min-width="240" show-overflow-tooltip />
          <el-table-column prop="action" label="建议" min-width="200" show-overflow-tooltip />
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="resolveDiff(row)">已处理</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="冲突对峙 (M2)" name="conflicts">
        <div v-for="c in conflicts" :key="c.id" class="conflict-row">
          <div class="cf-query">📌 查询: {{ c.query }}</div>
          <div class="cf-side">
            <el-tag size="small" type="warning">wiki</el-tag>
            {{ c.wiki_says?.claim?.slice(0, 160) }}
            <div class="cf-src">{{ c.wiki_says?.source }}</div>
          </div>
          <div class="cf-side">
            <el-tag size="small" type="success">rag</el-tag>
            {{ c.rag_says?.claim?.slice(0, 160) }}
            <div class="cf-src">{{ c.rag_says?.source }}</div>
          </div>
          <div class="cf-ops">
            <el-button size="small" @click="resolveConflict(c, 'wiki')">采信 wiki</el-button>
            <el-button size="small" @click="resolveConflict(c, 'rag')">采信 rag</el-button>
            <el-button size="small" @click="resolveConflict(c, 'both')">两者都对</el-button>
            <el-button size="small" @click="resolveConflict(c, 'none')">都不对</el-button>
          </div>
        </div>
        <el-empty v-if="!conflicts.length" description="暂无开放冲突" />
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, listProjects } from '../api'

const projectId = ref('')
const projects = ref<Array<{ id: string }>>([])
const tab = ref('diffs')
const diffs = ref<any[]>([])
const conflicts = ref<any[]>([])

onMounted(async () => {
  try {
    projects.value = await listProjects()
    projectId.value = projects.value[0]?.id ?? ''
    await refresh()
  } catch (e: any) {
    ElMessage.warning('后端未连接: ' + (e?.message ?? e))
  }
})

async function refresh() {
  if (!projectId.value) return
  try {
    const [d, c] = await Promise.all([
      api.get(`/projects/${projectId.value}/ledger/diffs`),
      api.get(`/projects/${projectId.value}/ledger/conflicts`),
    ])
    diffs.value = d.data
    conflicts.value = c.data
  } catch { /* ignore */ }
}

async function resolveDiff(row: any) {
  await api.post(`/projects/${projectId.value}/ledger/diffs/${row.id}/resolve`, { action: 'resolve' })
  ElMessage.success('已标记处理')
  await refresh()
}

async function resolveConflict(c: any, resolution: string) {
  await api.post(`/projects/${projectId.value}/ledger/conflicts/${c.id}/resolve`, {
    action: 'resolve', resolution,
  })
  ElMessage.success('裁决已记录')
  await refresh()
}
</script>

<style scoped>
.conflict-row { padding: 12px; border: 1px solid #e4e7ed; border-radius: 6px; margin-bottom: 10px; }
.cf-query { font-size: 13px; color: #606266; margin-bottom: 8px; }
.cf-side { font-size: 13px; margin: 4px 0; color: #303133; }
.cf-src { color: #909399; font-size: 12px; }
.cf-ops { margin-top: 8px; }
</style>
