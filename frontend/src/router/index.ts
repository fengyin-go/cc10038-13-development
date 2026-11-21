import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import MissingValueView from '@/views/MissingValueView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径要先于 /:moduleKey 注册，避免被模块工作台通配吃掉。
    { path: '/missing-values', name: 'missing-values', component: MissingValueView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
