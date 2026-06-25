import { computed, ref } from 'vue'

// ── 认证状态 (全应用共享的响应式 token) ──────────────────────
// localStorage 本身不是响应式的, 直接用 computed(() => localStorage.getItem(...))
// 在登录/登出后不会重新求值, 导致导航栏不随登录态切换。
// 这里用一个模块级 ref 作为单一响应式真相源, localStorage 仅作持久化。
const token = ref<string | null>(localStorage.getItem('access_token'))

export const isLoggedIn = computed(() => !!token.value)

/** 登录: 写入 token (响应式状态 + localStorage 持久化) */
export function setToken(value: string) {
  token.value = value
  localStorage.setItem('access_token', value)
}

/** 登出: 清除 token (响应式状态 + localStorage) */
export function clearToken() {
  token.value = null
  localStorage.removeItem('access_token')
}
