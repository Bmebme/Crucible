<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 16px">
      <template #header>
        L3 审核队列
        <el-select v-model="projectId" style="width: 140px; margin-left: 12px" @change="refresh">
          <el-option v-for="p in projects" :key="p.id" :label="p.id" :value="p.id" />
        </el-select>
        <span class="hint">LLM 判定等价的名字对 → 人工确认后回写 kb-aliases.yaml</span>
      </template>

      <div v-if="reviews.length" class="review-list">
        <div v-for="r in reviews" :key="r.id" class="review-card">
          <div class="pair">
            <span class="name-a">{{ r.name_a }}</span>
            <el-icon class="swap"><span>⇄</span></el-icon>
            <span class="name-b">{{ r.name_b }}</span>
          </div>
          <div class="ops">
            <el-button size="small" type="success" @click="resolve(r, 'approve')">确认等价（回写词典）</el-button>
            <el-button size="small" type="danger" plain @click="resolve(r, 'reject')">拒绝</el-button>
          </div>
        </div>
      </div>
      <el-empty v-else description="暂无待审核项 (跑一次枚举查询, L3 判定会自动进队)" />
    </el-card>

    <el-card shadow="never">
      <template #header>kb-aliases.yaml（当前词典）</template>
      <pre class="yaml">{{ yamlText || '加载中…' }}</pre>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, listProjects } from '../api'

const projectId = ref('')
const projects = ref<Array<{ id: string }>>([])
const reviews = ref<any[]>([])
const yamlText = ref('')

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
    const r = await api.get(`/projects/${projectId.value}/alias-reviews`)
    reviews.value = r.data
    await loadYaml()
  } catch { /* ignore */ }
}

async function resolve(r: any, action: string) {
  const { data } = await api.post(
    `/projects/${projectId.value}/alias-reviews/${r.id}/resolve`, { action },
  )
  if (data.ok) {
    ElMessage.success(action === 'approve' ? '已回写词典' : '已拒绝')
    await refresh()
  } else {
    ElMessage.error(data.error ?? '操作失败')
  }
}

async function loadYaml() {
  try {
    const r = await api.get(`/projects/${projectId.value}/aliases/file`)
    yamlText.value = r.data.content
  } catch { /* ignore */ }
}
</script>

<style scoped>
.hint { font-size: 12px; color: #909399; margin-left: 12px; }
.review-card {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; border: 1px solid #e4e7ed; border-radius: 6px; margin-bottom: 8px;
}
.pair { font-size: 15px; }
.name-a, .name-b { font-family: ui-monospace, monospace; }
.swap { margin: 0 10px; color: #909399; }
.yaml {
  background: #f8fafc; padding: 12px; border-radius: 6px;
  font-size: 12px; line-height: 1.6; overflow: auto; max-height: 480px;
}
</style>
