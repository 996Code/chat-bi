<template>
  <!--
    App.vue — 顶级布局组件

    架构职责:
      1. 提供全局导航栏 (el-header + router-link 菜单)
      2. 通过 <router-view /> 渲染当前路由对应的页面
      3. 管理登录态与登出流程

    导航架构:
      - 使用 router-link custom v-slot 模式, 而不是默认的 <a> 标签,
        这样可以把导航项渲染为 <span> 而非 <a>, 方便统一样式控制
      - 所有路由都平铺在导航栏中, 没有嵌套路由或子菜单
      - 导航栏 sticky 固定在顶部, 登录后可见, 未登录时隐藏

    数据流:
      isLoggedIn 来自 composables/useAuth 的模块级 ref,
      通过响应式 token 自动驱动导航栏显隐,
      无需额外的事件总线或状态管理。
  -->
  <div id="app">
    <!-- 导航栏 (已登录才显示) -->
    <el-header v-if="isLoggedIn" class="nav-bar" height="56px">
      <div class="nav-left">
        <span class="logo">📊 ChatBI v2</span>
      </div>
      <div class="nav-menu">
        <router-link to="/chat" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">对话</span>
        </router-link>
        <router-link to="/datasources" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">数据源</span>
        </router-link>
        <router-link to="/dashboard" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">看板</span>
        </router-link>
        <router-link to="/semantic" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">语义层</span>
        </router-link>
        <router-link to="/history" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">历史</span>
        </router-link>
        <router-link to="/observability" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">系统</span>
        </router-link>
        <router-link to="/skills" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">规则</span>
        </router-link>
        <router-link to="/memory" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">记忆</span>
        </router-link>
      </div>
      <div class="nav-right">
        <el-button text size="small" @click="logout">退出</el-button>
      </div>
    </el-header>

    <main :class="{ 'with-nav': isLoggedIn }">
      <!--
        router-view 是 Vue Router 的渲染出口:
        根据当前 URL 匹配路由配置, 动态渲染对应视图组件。
        所有页面组件 (ChatView, DataSourceView 等) 都在此渲染。
        keep-alive 未使用, 因为各页面不需要缓存状态。
      -->
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 顶级布局逻辑
 *
 * 使用 Vue 3 <script setup> 语法:
 * - 无需 export default, 顶层绑定自动暴露给模板
 * - 导入的 composable 函数可直接在模板中使用
 *
 * 认证状态管理:
 * - isLoggedIn: 响应式计算属性, 驱动导航栏显隐
 * - clearToken: 清除本地 token, 登出后路由守卫跳转到 /login
 * - 认证逻辑集中在 composables/useAuth, 保持 App.vue 简洁
 */
import { useRouter } from 'vue-router'
import { isLoggedIn, clearToken } from '@/composables/useAuth'

const router = useRouter()

/**
 * 登出流程:
 * 1. 清除本地存储的 token (响应式状态 + localStorage)
 * 2. 路由跳转到登录页
 * 3. 路由守卫 router.beforeEach 会自动处理后续导航
 * 注意: 不清除 refresh_token, 由父组件或登录页统一处理
 */
function logout() {
  clearToken()
  router.push('/login')
}
</script>

<style>
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
    'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  background: #f5f7fa;
}
#app {
  min-height: 100vh;
}
.nav-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  position: sticky;
  top: 0;
  z-index: 100;
}
.logo {
  font-size: 1.1rem;
  font-weight: 700;
  color: #303030;
}
.nav-menu {
  display: flex;
  gap: 8px;
}
.nav-item {
  padding: 6px 16px;
  cursor: pointer;
  border-radius: 4px;
  color: #606266;
  font-size: 0.95rem;
  transition: all 0.2s;
}
.nav-item:hover {
  background: #f5f7fa;
  color: #409eff;
}
.nav-item.active {
  color: #409eff;
  background: #ecf5ff;
}
.with-nav {
  min-height: calc(100vh - 56px);
}
</style>
