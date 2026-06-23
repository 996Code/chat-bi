<template>
  <div id="app">
    <!-- 导航栏 (已登录才显示) -->
    <el-header v-if="isLoggedIn" class="nav-bar" height="56px">
      <div class="nav-left">
        <span class="logo">📊 ChatBI v2</span>
      </div>
      <div class="nav-menu">
        <router-link to="/datasources" custom v-slot="{ navigate, isActive }">
          <span :class="['nav-item', { active: isActive }]" @click="navigate">数据源</span>
        </router-link>
      </div>
      <div class="nav-right">
        <el-button text size="small" @click="logout">退出</el-button>
      </div>
    </el-header>

    <main :class="{ 'with-nav': isLoggedIn }">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const isLoggedIn = computed(() => !!localStorage.getItem('access_token'))

function logout() {
  localStorage.removeItem('access_token')
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
