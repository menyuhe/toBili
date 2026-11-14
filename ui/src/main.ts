import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

import App from './App.vue'
import Standby from './views/Standby.vue'
import Overview from './views/Overview.vue'
import Sources from './views/Sources.vue'
import { extraRoutes } from './extensions'
import Pool from './views/Pool.vue'
import Tidy from './views/Tidy.vue'
import Results from './views/Results.vue'
import Activity from './views/Activity.vue'
import Inbox from './views/Inbox.vue'
import Campaigns from './views/Campaigns.vue'
import Auto from './views/Auto.vue'
import Settings from './views/Settings.vue'
import Blocks from './views/Blocks.vue'
import './styles.css'

// hash 模式：这个应用可能从 file:// 或者桌面壳里的任意路径打开，
// history 模式在没有服务端 rewrite 的场景下刷新就 404。hash 到哪儿都能跑。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {path:'/standby',component:Standby,meta:{title:'挂机运作'}},
    { path: '/', name: 'overview', component: Overview, meta: { title: '运营中心' } },
    { path: '/inbox', name: 'inbox', component: Inbox, meta: { title: '消息' } },
    { path: '/campaigns', name: 'campaigns', component: Campaigns, meta: { title: '活动奖品' } },
    { path: '/shelf', component: Campaigns, meta:{title:'往期架'} },
    { path: '/cleaned', component: Campaigns, meta:{title:'已清理'} },
    { path: '/auto', name: 'auto', component: Auto, meta: { title: '参与排期' } },
    { path: '/sources', name: 'sources', component: Sources, meta: { title: '订阅源' } },
    ...extraRoutes,
    { path: '/pool', name: 'pool', component: Pool, meta: { title: '计划单' } },
    { path: '/tidy', name: 'tidy', component: Tidy, meta: { title: '清理清单' } },
    { path: '/results', name: 'results', component: Results, meta: { title: '开奖核验' } },
    { path: '/activity', name: 'activity', component: Activity, meta: { title: '运行记录' } },
    { path: '/settings', name: 'settings', component: Settings, meta: { title: '设置' } },
    { path: '/blocks', component: Blocks, meta: { title: 'UP 黑名单' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

createApp(App).use(router).mount('#app')
