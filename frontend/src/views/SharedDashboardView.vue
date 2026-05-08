<template>
  <div class="shared-dashboard-view">
    <!-- Password gate -->
    <div v-if="needsPassword && !dataLoaded" class="password-gate">
      <el-card class="password-card">
        <h3 style="text-align: center; margin-bottom: 16px;">{{ dashboardName }}</h3>
        <p style="text-align: center; color: #909399; margin-bottom: 20px;">此看板需要密码才能访问</p>
        <el-form @submit.prevent="submitPassword">
          <el-form-item>
            <el-input
              v-model="password"
              placeholder="请输入访问密码"
              show-password
              @keyup.enter="submitPassword"
            />
          </el-form-item>
          <el-button
            type="primary"
            style="width: 100%"
            :loading="loading"
            @click="submitPassword"
          >
            访问看板
          </el-button>
        </el-form>
      </el-card>
    </div>

    <!-- Loading -->
    <div v-else-if="loading" class="loading-state">
      <el-icon :size="40" class="is-loading"><Loading /></el-icon>
      <p>加载中...</p>
    </div>

    <!-- Error -->
    <div v-else-if="errorMsg" class="error-state">
      <el-icon :size="40" color="#f56c6c"><WarningFilled /></el-icon>
      <p>{{ errorMsg }}</p>
      <el-button type="primary" @click="$router.push('/login')">去登录</el-button>
    </div>

    <!-- Dashboard content -->
    <div v-else-if="dataLoaded" class="shared-content">
      <div class="shared-header">
        <h2>{{ dashboardName }}</h2>
        <span class="shared-badge">分享看板</span>
      </div>
      <div class="shared-body">
        <div class="widget-grid" :style="gridStyle">
          <DashboardWidget
            v-for="widget in widgets"
            :key="widget.id"
            :widget="widget"
            :cell-size="cellSize"
            :gap="gridGap"
            :col-count="colCount"
            :data-widget-id="widget.id"
            :readonly="true"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Loading, WarningFilled } from '@element-plus/icons-vue'
import api from '@/api'
import DashboardWidget from '@/components/DashboardWidget.vue'

const route = useRoute()
const shareToken = computed(() => route.params.token as string)

const loading = ref(true)
const dataLoaded = ref(false)
const needsPassword = ref(false)
const password = ref('')
const errorMsg = ref('')
const dashboardName = ref('')
const widgets = ref<any[]>([])

const colCount = 12
const gridGap = 16
const gridRowHeight = 80
const cellSize = ref(100)

const gridStyle = computed(() => ({
  gridTemplateColumns: `repeat(${colCount}, 1fr)`,
  gridAutoRows: `${gridRowHeight}px`,
  gap: `${gridGap}px`,
}))

function updateCellSize() {
  const container = document.querySelector('.shared-body') as HTMLElement
  if (container) {
    cellSize.value = Math.floor((container.clientWidth - (colCount - 1) * gridGap) / colCount)
  }
}

async function fetchSharedData(pwd?: string) {
  loading.value = true
  errorMsg.value = ''
  try {
    const params: any = {}
    if (pwd) params.password = pwd
    const res = await api.get(`/dashboards/shared/${shareToken.value}`, { params })
    dashboardName.value = res.data.name
    widgets.value = res.data.widgets || []
    dataLoaded.value = true
    needsPassword.value = false
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    if (detail?.code === 'PASSWORD_REQUIRED') {
      needsPassword.value = true
    } else if (detail?.code === 'EXPIRED') {
      errorMsg.value = '分享链接已过期'
    } else if (detail?.code === 'WRONG_PASSWORD') {
      ElMessage.error('密码错误')
      needsPassword.value = true
    } else {
      errorMsg.value = detail?.message || '看板不存在或无法访问'
    }
  } finally {
    loading.value = false
  }
}

async function submitPassword() {
  if (!password.value.trim()) {
    ElMessage.warning('请输入密码')
    return
  }
  await fetchSharedData(password.value)
}

onMounted(async () => {
  await fetchSharedData()
  window.addEventListener('resize', updateCellSize)
  setTimeout(updateCellSize, 100)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateCellSize)
})
</script>

<style scoped>
.shared-dashboard-view {
  min-height: 100vh;
  background: #f5f7fa;
}

.password-gate {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}

.password-card {
  width: 400px;
}

.loading-state,
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  gap: 12px;
  color: #909399;
}

.shared-content {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.shared-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  background: white;
  border-bottom: 1px solid #e4e7ed;
}

.shared-header h2 {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.shared-badge {
  font-size: 12px;
  color: #409eff;
  background: #ecf5ff;
  padding: 2px 8px;
  border-radius: 4px;
}

.shared-body {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}

.widget-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  grid-auto-rows: v-bind(gridRowHeight + 'px');
  gap: 16px;
  position: relative;
  min-height: 400px;
}
</style>
