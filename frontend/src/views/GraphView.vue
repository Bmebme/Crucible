<template>
  <el-card shadow="never" class="graph-card">
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
import { nextTick, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { api, listProjects } from '../api'

const projectId = ref('')
const projects = ref<Array<{ id: string }>>([])
const chart = ref<HTMLElement>()
let onResize: () => void

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

  echarts.getInstanceByDom(chart.value!)?.dispose()
  const inst = echarts.init(chart.value!)
  const small = nodes.length < 30
  inst.setOption({
    tooltip: {},
    // 图例放顶部: 底部不留图例条, 小图视觉不再"下面空一大块"
    legend: [{ data: cats.map((c: any) => c.name), type: 'scroll', top: 0 }],
    series: [{
      type: 'graph', layout: 'force', roam: true,
      data: nodes, links: edges, categories: cats,
      // 小图加大斥力铺开画布, 大图保持紧凑 (空白感主要来自小图)
      force: small
        ? { repulsion: 420, edgeLength: [30, 90] }
        : { repulsion: 260, edgeLength: [40, 140] },
      layoutCenter: ['50%', '50%'],
      label: { show: true, fontSize: 10, position: 'right' },
      lineStyle: { opacity: 0.35 },
      emphasis: { focus: 'adjacency' },
    }],
  })
  window.removeEventListener('resize', onResize)
  onResize = () => inst.resize()
  window.addEventListener('resize', onResize)
  // 高度变化后同步画布
  await nextTick()
  inst.resize()
}
</script>

<style scoped>
/* 贴合视口: App.vue 已把 main 锚定在剩余高度, 这里吃满 100% 即可,
   任意窗口大小/缩放/顶栏高度都自适应 (旧 calc(100vh-108px) 是
   MacBook 像素假设, 内网 Chrome 上出滚动条/截断) */
.graph-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.graph-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 12px;
  overflow: hidden;
}
.chart { flex: 1; min-height: 0; }
.hint { font-size: 12px; color: #909399; margin-left: 12px; }
</style>
