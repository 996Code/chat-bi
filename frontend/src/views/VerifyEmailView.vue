<template>
  <div class="verify-email-view">
    <div class="verify-card">
      <el-icon :size="64" :color="status === 'success' ? '#67c23a' : status === 'error' ? '#f56c6c' : '#909399'">
        <CircleCheckFilled v-if="status === 'success'" />
        <CircleCloseFilled v-else-if="status === 'error'" />
        <Loading v-else class="is-loading" />
      </el-icon>

      <h2 v-if="status === 'success'">邮箱验证成功</h2>
      <h2 v-else-if="status === 'error'">验证链接已过期</h2>
      <h2 v-else>正在验证...</h2>

      <p v-if="status === 'success'">
        您的邮箱已成功验证，即将跳转到主页...
      </p>
      <p v-else-if="status === 'error'">
        该验证链接已过期或无效。您可以重新登录获取新的验证链接。
      </p>
      <p v-else>
        正在验证您的邮箱，请稍候...
      </p>

      <div class="actions" v-if="status === 'error'">
        <el-button type="primary" @click="router.push('/login')">返回登录</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api from '@/api'
import { CircleCheckFilled, CircleCloseFilled, Loading } from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()
const status = ref<'loading' | 'success' | 'error'>('loading')

onMounted(async () => {
  const token = (route.query.token as string) || ''
  if (!token) {
    status.value = 'error'
    return
  }

  try {
    await api.post('/auth/verify-email', { token })
    status.value = 'success'
    setTimeout(() => router.push('/'), 2000)
  } catch {
    status.value = 'error'
  }
})
</script>

<style scoped>
.verify-email-view {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.verify-card {
  background: white;
  border-radius: 16px;
  padding: 48px 40px;
  text-align: center;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
  max-width: 420px;
  width: 90%;
}

.verify-card h2 {
  margin: 20px 0 8px;
  font-size: 22px;
  color: #303133;
}

.verify-card p {
  color: #909399;
  font-size: 14px;
  line-height: 1.6;
  margin-bottom: 24px;
}

.actions {
  display: flex;
  justify-content: center;
  gap: 12px;
}
</style>
