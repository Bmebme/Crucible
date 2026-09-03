<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 16px">
      <template #header>注册新项目</template>
      <el-form label-width="150px" style="max-width: 640px">
        <el-form-item label="项目 ID">
          <el-input v-model="form.id" placeholder="例如 mae" />
        </el-form-item>
        <el-form-item label="项目路径">
          <el-input v-model="form.path" placeholder="/绝对/路径/到/项目" />
        </el-form-item>
        <el-form-item label="llm-wiki 项目 ID">
          <el-input v-model="form.wiki_project_id" placeholder="llm-wiki 侧的项目 id (默认同项目 ID)" />
        </el-form-item>
        <el-form-item label="LightRAG 工作目录">
          <el-input v-model="form.rag_workdir" placeholder="留空 = <项目路径>/.lightrag" />
        </el-form-item>
        <el-form-item label="对齐模式">
          <el-select v-model="form.alias_mode" style="width: 140px">
            <el-option label="l2+l3" value="l2+l3" />
            <el-option label="l3" value="l3" />
            <el-option label="off" value="off" />
          </el-select>
        </el-form-item>
        <el-button type="primary" :loading="saving" @click="save">注册</el-button>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <template #header>已注册项目</template>
      <el-table :data="projects" size="small">
        <el-table-column prop="id" label="ID" width="140" />
        <el-table-column prop="path" label="路径" min-width="280" show-overflow-tooltip />
        <el-table-column prop="alias_mode" label="对齐模式" width="100" />
        <el-table-column prop="created_at" label="注册时间" width="180">
          <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 19) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listProjects, registerProject } from '../api'

const projects = ref<any[]>([])
const saving = ref(false)
const form = reactive({ id: '', path: '', wiki_project_id: '', rag_workdir: '', alias_mode: 'l2+l3' })

onMounted(refresh)

async function refresh() {
  try { projects.value = await listProjects() } catch (e: any) {
    ElMessage.warning('后端未连接: ' + (e?.message ?? e))
  }
}

async function save() {
  if (!form.id || !form.path) { ElMessage.info('填写项目 ID 和路径'); return }
  saving.value = true
  try {
    await registerProject({ ...form })
    ElMessage.success('注册成功')
    await refresh()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail ?? e?.message)
  } finally {
    saving.value = false
  }
}
</script>
