<template>
  <el-card shadow="never">
    <template #header>
      实体图浏览
      <el-select v-model="projectId" style="width: 140px; margin-left: 12px" @change="load">
        <el-option v-for="p in projects" :key="p.id" :label="p.id" :value="p.id" />
      </el-select>
      <span class="hint">LightRAG 实体关系图 (按度数取中心子图, 悬停看描述)</span>
    </template>
    <div ref="chart" class="chart" />
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { api, listProjects } from '../api'

const projectId = ref('')
const projects = ref<Array<{ id: string }>>([])
const chart = ref<HTMLElement>()

onMounted(async () => {
  try {
    projects.value = await listProjects()
    projectId.value = projects.value[0]?.id ?? ''
    await load()
  } catch { /* ignore */ }
})

async function load() {
  if (!projectId.value) return
  const { data } = await api.get(`/projects/${projectId.value}/graph`)
  const nodes = data.nodes.map((n: any) => ({
    id: n.id, name: n.id,
    symbolSize: Math.min(8 + n.degree * 2, 32),
    category: n.type,
    tooltip: { formatter: `${n.id} [${n.type}]<br/>${n.description}` },
  }))
  const edges = data.edges.map((e: any) => ({ source: e.s, target: e.t }))
  const cats = [...new Set(data.nodes.map((n: any) => n.type))].map((t: any) => ({ name: t }))

  const inst = echarts.init(chart.value!)
  inst.setOption({
    tooltip: {},
    legend: [{ data: cats.map((c: any) => c.name), type: 'scroll', bottom: 0 }],
    series: [{
      type: 'graph', layout: 'force', roam: true,
      data: nodes, links: edges, categories: cats,
      force: { repulsion: 260, edgeLength: [40, 140] },
      label: { show: true, fontSize: 10, position: 'right' },
      lineStyle: { opacity: 0.35 },
      emphasis: { focus: 'adjacency' },
    }],
  })
  window.addEventListener('resize', () => inst.resize())
}
</script>

<style scoped>
.chart { height: 680px; }
.hint { font-size: 12px; color: #909399; margin-left: 12px; }
</style>
