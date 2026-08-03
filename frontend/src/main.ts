/**
 * ChatBI v2 前端入口 — 应用引导与插件注册
 *
 * 架构职责:
 *   1. 创建 Vue 应用实例
 *   2. 注册全局插件 (Vue Router, Element Plus)
 *   3. 挂载到 DOM 根节点 (#app)
 *
 * 设计决策:
 *   - 使用 Element Plus 作为 UI 组件库 (一致性、可访问性、按需引入)
 *   - Vue Router 作为唯一路由管理器, 无状态管理库 (Pinia/Vuex),
 *     因为应用状态主要在 URL 和 localStorage 中, 没有跨组件共享的复杂状态树
 *   - 所有数据流通过 props/emits + API 层传递, 保持组件树纯净
 */
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'

// 创建 Vue 应用实例, 这是整个前端应用的根节点
const app = createApp(App)

// 注册 Vue Router: 管理页面导航、路由守卫、懒加载
app.use(router)

// 注册 Element Plus: 提供 el-* 组件 (el-table, el-dialog, el-form 等)
// 注意: 当前为全量引入, 生产环境可切换为按需引入以减小打包体积
app.use(ElementPlus)

// 挂载到 DOM: 对应 index.html 中的 <div id="app"></div>
// mount 之后, Vue 接管 DOM 渲染, 所有后续操作都在 Vue 响应式系统内完成
app.mount('#app')
