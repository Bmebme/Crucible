<template>
  <el-container class="app">
    <el-header class="header">
      <div class="brand">⚗️ Crucible <span class="sub">漏洞验证知识融合台</span></div>
      <el-menu mode="horizontal" :default-active="$route.path" router class="menu">
        <el-menu-item index="/">查询工作台</el-menu-item>
        <el-menu-item index="/upload">上传中心</el-menu-item>
        <el-menu-item index="/ledger">知识台账</el-menu-item>
        <el-menu-item index="/aliases">对齐管理</el-menu-item>
        <el-menu-item index="/graph">实体图</el-menu-item>
        <el-menu-item index="/projects">项目中心</el-menu-item>
      </el-menu>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
</script>

<style>
body { margin: 0; background: #f5f7fa; font-family: -apple-system, "PingFang SC", sans-serif; }
.header {
  display: flex; align-items: center; gap: 32px;
  background: #1d2129; color: #fff; padding: 0 24px;
}
.brand { font-size: 18px; font-weight: 700; white-space: nowrap; }
.brand .sub { font-size: 12px; font-weight: 400; opacity: .65; margin-left: 8px; }
.menu { background: transparent; border-bottom: none; flex: 1; }
.menu .el-menu-item { color: #cfd3dc; }
.menu .el-menu-item.is-active { color: #fff; border-bottom-color: #409eff; }
.el-main { padding: 20px 24px; }
</style>
