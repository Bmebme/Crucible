<template>
  <div>
    <el-card shadow="never" class="upload-card">
      <template #header>文档上传（双通道: wiki + LightRAG）</template>
      <el-form :inline="true">
        <el-form-item label="项目">
          <el-select v-model="projectId" style="width: 160px">
            <el-option v-for="p in projects" :key="p.id" :label="p.id" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="子目录">
          <el-select v-model="subdir" style="width: 160px">
            <el-option label="(默认)" value="" />
            <el-option label="verification（验证记录）" value="verification" />
          </el-select>
        </el-form-item>
      </el-form>

      <el-upload
        drag multiple :auto-upload="false"
        :on-change="onFiles" :file-list="fileList"
        accept=".md,.txt,.pdf,.docx,.ppt,.pptx,.png,.jpg,.jpeg"
      >
        <div class="el-upload__text">拖拽文件到这里，或 <em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">
            统一源: 原件存 raw/originals, MinerU 标准化 md 进 raw/sources,
            并触发 llm-wiki 生成知识页 (wiki 层)。pdf/docx 经 MinerU 解析
            (CPU 上分钟级), 任务卡片实时显示阶段。验证记录选 verification。
          </div>
        </template>
      </el-upload>

      <el-button type="primary" :loading="uploading" :disabled="!fileList.length"
        style="margin-top: 12px" @click="upload">
        上传 {{ fileList.length }} 个文件
      </el-button>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        摄入任务
        <el-tag v-if="polling" size="small" type="warning" style="margin-left: 8px">轮询中…</el-tag>
        <el-button size="small" style="float: right" @click="refresh">刷新</el-button>
      </template>
      <el-table :data="jobs" size="small">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="filename" label="文件" min-width="180" show-overflow-tooltip />
        <el-table-column prop="kind" label="类型" width="70" />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusColor(row.status)">{{ stageLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="阶段时间线" min-width="260">
          <template #default="{ row }">
            <span class="timeline">{{ stageTimeline(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="失败原因" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error" class="err-text">{{ row.error }}</span>
            <span v-else style="color: #bbb">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="wiki_path" label="落盘路径" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listJobs, listProjects, uploadDocument } from '../api'

const STAGE_LABEL: Record<string, string> = {
  uploaded: '已接收', converting: 'MinerU 解析中', converted: '转换完成',
  sourced: '源写入', wiki_indexed: '写入 wiki', rag_ingesting: '向量化中', done: '完成', failed: '失败',
}
const STAGE_ORDER = ['uploaded', 'converting', 'converted', 'sourced', 'wiki_indexed', 'rag_ingesting', 'done', 'failed']
const ACTIVE = ['uploaded', 'converting', 'converted', 'sourced', 'wiki_indexed', 'rag_ingesting']

const projectId = ref('')
const projects = ref<Array<{ id: string }>>([])
const subdir = ref('')
const fileList = ref<any[]>([])
const uploading = ref(false)
const jobs = ref<any[]>([])
const polling = ref(false)
let timer: any = null

onMounted(async () => {
  try {
    projects.value = await listProjects()
    projectId.value = projects.value[0]?.id ?? ''
    await refresh()
  } catch (e: any) {
    ElMessage.warning('后端未连接: ' + (e?.message ?? e))
  }
})

onBeforeUnmount(stopPolling)

function onFiles(file: any) {
  fileList.value.push(file)
}

function stageLabel(s: string) {
  return STAGE_LABEL[s] ?? s
}

function statusColor(s: string) {
  return { done: 'success', failed: 'danger', converting: 'warning', rag_ingesting: 'warning', uploaded: 'info', converted: 'primary', wiki_indexed: 'primary' }[s] ?? 'info'
}

function fmt(ts: string) {
  // stages 时间戳为 UTC ISO; 转本地 hh:mm:ss
  if (!ts) return ''
  const d = new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z')
  return isNaN(d.getTime()) ? ts : d.toLocaleTimeString('zh-CN', { hour12: false })
}

function stageTimeline(row: any) {
  const stages: Record<string, string> = row.detail?.stages ?? {}
  const parts: string[] = []
  for (const k of STAGE_ORDER) {
    if (stages[k]) parts.push(`${stageLabel(k)} ${fmt(stages[k])}`)
  }
  if (!parts.length && row.created_at) parts.push(`创建 ${(row.created_at || '').replace('T', ' ').slice(0, 19)}`)
  return parts.join(' → ')
}

async function refresh() {
  if (!projectId.value) return
  try { jobs.value = await listJobs(projectId.value) } catch { /* ignore */ }
}

function startPolling() {
  stopPolling()
  polling.value = true
  timer = setInterval(async () => {
    await refresh()
    const hasActive = jobs.value.some((j) => ACTIVE.includes(j.status))
    if (!hasActive) {
      stopPolling()
      ElMessage.success('全部任务已结束')
    }
  }, 2500)
}

function stopPolling() {
  polling.value = false
  if (timer) { clearInterval(timer); timer = null }
}

async function upload() {
  uploading.value = true
  let ok = 0
  try {
    // 后端已异步化: 每个文件立即返回 job_id, 管线后台跑
    for (const f of fileList.value) {
      try {
        const r = await uploadDocument(projectId.value, f.raw, subdir.value)
        if (r.ok) ok += 1
        else ElMessage.error(`${f.name}: ${r.error ?? '失败'}`)
      } catch (e: any) {
        ElMessage.error(`${f.name}: ${e?.response?.data?.detail ?? e?.message}`)
      }
    }
    ElMessage.success(`已接收 ${ok}/${fileList.value.length} 个任务, 下方实时显示阶段`)
    fileList.value = []
    await refresh()
    startPolling()
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.upload-card .el-upload { width: 100%; }
.timeline { font-size: 12px; color: #666; }
.err-text { color: #f56c6c; font-size: 12px; }
</style>
