/**
 * 认证状态管理 — 响应式 token 与登录态
 *
 * 架构职责:
 *   1. 提供全局共享的响应式登录状态 (isLoggedIn)
 *   2. 管理 token 的读写 (setToken / clearToken)
 *   3. 作为 localStorage 的响应式桥接层
 *
 * 为什么需要这个模块:
 *   - localStorage 本身不是响应式的, 直接用 computed(() => localStorage.getItem(...))
 *     在登录/登出后不会重新求值, 导致导航栏不随登录态切换。
 *   - 这里用一个模块级 ref 作为单一响应式真相源, localStorage 仅作持久化。
 *   - 组件通过 import { isLoggedIn } from '@/composables/useAuth' 直接使用,
 *     不需要 Pinia/Vuex 等状态管理库。
 *
 * 数据流:
 *   LoginView → setToken(token) → token.value 更新 → isLoggedIn 重新计算
 *   → App.vue 导航栏自动显隐 / 路由守卫自动跳转
 *
 * 注意: 这个模块只管理 access_token, refresh_token 由 API 拦截器直接管理。
 * 这样符合职责分离: 组件只关心"是否登录", 不关心 refresh 逻辑。
 */
import { computed, ref } from 'vue'

// ── 认证状态 (全应用共享的响应式 token) ──────────────────────
// localStorage 本身不是响应式的, 直接用 computed(() => localStorage.getItem(...))
// 在登录/登出后不会重新求值, 导致导航栏不随登录态切换。
// 这里用一个模块级 ref 作为单一响应式真相源, localStorage 仅作持久化。
const token = ref<string | null>(localStorage.getItem('access_token'))

/**
 * 是否已登录 — 响应式计算属性
 *
 * 当 token 为非空字符串时返回 true, 否则返回 false。
 * 所有依赖此状态的组件 (App.vue 导航栏等) 都会自动响应变化。
 * 不需要手动订阅或事件广播。
 */
export const isLoggedIn = computed(() => !!token.value)

/**
 * 登录: 写入 token (响应式状态 + localStorage 持久化)
 *
 * 调用方: LoginView 或 devAuth 获取 token 后调用。
 * 副作用:
 *   1. 更新模块级 ref, 触发所有 isLoggedIn 依赖的组件重新渲染
 *   2. 写入 localStorage, 确保页面刷新后 token 不丢失
 *   3. 后续 API 请求的拦截器会自动从 localStorage 读取
 */
export function setToken(value: string) {
  token.value = value
  localStorage.setItem('access_token', value)
}

/**
 * 登出: 清除 token (响应式状态 + localStorage)
 *
 * 调用方: App.vue 的 logout 函数, 或 API 401 刷新失败后。
 * 副作用:
 *   1. token.value = null, 触发 isLoggedIn 变为 false
 *   2. 导航栏隐藏, 路由守卫在下次导航时跳转到 /login
 *   3. 后续 API 请求不再携带 Authorization header
 *   4. 401 响应拦截器会返回 reject, 由调用方决定是否显示错误
 *
 * 注意: 此函数不清除 refresh_token, 由调用方 (client.ts 的 _tryRefresh
 * 失败后) 清除。这是因为 refresh_token 的生命周期独立于登录态。
 */
export function clearToken() {
  token.value = null
  localStorage.removeItem('access_token')
}
