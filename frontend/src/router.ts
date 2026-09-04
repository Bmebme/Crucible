import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'query', component: () => import('./views/QueryConsole.vue'), meta: { title: '查询工作台' } },
    { path: '/upload', name: 'upload', component: () => import('./views/UploadView.vue'), meta: { title: '上传中心' } },
    { path: '/ledger', name: 'ledger', component: () => import('./views/LedgerView.vue'), meta: { title: '知识台账' } },
    { path: '/aliases', name: 'aliases', component: () => import('./views/AliasReviewView.vue'), meta: { title: '对齐管理' } },
    { path: '/projects', name: 'projects', component: () => import('./views/ProjectsView.vue'), meta: { title: '项目中心' } },
  ],
})
