<template>
  <div>
    <el-card shadow="never" class="upload-card">
      <template #header>文档上传（md / txt · 双通道: wiki + LightRAG）</template>
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
        accept=".md,.txt"
      >
        <div class="el-upload__text">拖拽文件到这里，或 <em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">
            支持 .md / .txt（10MB 以内）。pdf/docx 走 MinerU 管线（P3 后续）。
            验证记录请选 verification 子目录并带 verify_state frontmatter。
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
        <el-button size="small" style="float: right" @click="refresh">刷新</el-button>
      </template>
      <el-table :data="jobs" size="small">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="filename" label="文件" min-width="200" show-overflow-tooltip />
        <el-table-column prop="kind" label="类型" width="70" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="statusColor(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="wiki_path" label="wiki 路径" min-width="220" show-overflow-tooltip />
        <el-table-column prop="created_at" label="时间" width="180">
          <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 19) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listJobs, listProjects, uploadDocument } from '../api'

const projectId = ref('')
const projects = ref<Array<{ id: string }>>([])
const subdir = ref('')
const fileList = ref<any[]>([])
const uploading = ref(false)
const jobs = ref<any[]>([])

onMounted(async () => {
  try {
    projects.value = await listProjects()
    projectId.value = projects.value[0]?.id ?? ''
    await refresh()
  } catch (e: any) {
    ElMessage.warning('后端未连接: ' + (e?.message ?? e))
  }
})

function onFiles(file: any) {
  fileList.value.push(file)
}

function statusColor(s: string) {
  return { done: 'success', failed: 'danger', uploaded: 'info', wiki_indexed: 'primary', rag_ingested: 'primary' }[s] ?? 'info'
}

async function refresh() {
  if (!projectId.value) return
  try { jobs.value = await listJobs(projectId.value) } catch { /* ignore */ }
}

async function upload() {
  uploading.value = true
  let ok = 0
  try {
    for (const f of fileList.value) {
      try {
        const r = await uploadDocument(projectId.value, f.raw, subdir.value)
        if (r.ok) ok += 1
        else ElMessage.error(`${f.name}: ${r.error ?? '失败'}`)
      } catch (e: any) {
        ElMessage.error(`${f.name}: ${e?.response?.data?.detail ?? e?.message}`)
      }
    }
    ElMessage.success(`上传完成: ${ok}/${fileList.value.length} 成功`)
    fileList.value = []
    await refresh()
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.upload-card .el-upload { width: 100%; }
</style>
