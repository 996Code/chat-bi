<template>
  <div class="login-page">
    <div class="login-card">
      <h1 class="title">重置密码</h1>
      <p class="subtitle">输入你的邮箱，我们会发送重置链接</p>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="0" @submit.prevent="handleReset">
        <el-form-item prop="email">
          <el-input v-model="form.email" placeholder="邮箱地址" prefix-icon="Message" size="large" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" style="width: 100%" :loading="loading" native-type="submit">
            发送重置链接
          </el-button>
        </el-form-item>
      </el-form>
      <div class="links">
        <router-link to="/login">返回登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'
import type { FormInstance } from 'element-plus'
import { ElMessage } from 'element-plus'

const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)

const form = reactive({
  email: '',
})

const rules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }, { type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
}

async function handleReset() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await api.post('/auth/forgot-password', { email: form.email })
    ElMessage.success('重置链接已发送至你的邮箱')
    router.push('/login')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail?.message || '发送失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.login-card {
  width: 400px;
  padding: 40px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.title {
  text-align: center;
  font-size: 32px;
  margin: 0 0 8px;
  color: #1a1a2e;
}

.subtitle {
  text-align: center;
  color: #666;
  margin: 0 0 32px;
}

.links {
  margin-top: 16px;
  text-align: center;
}

.links a {
  color: #667eea;
  text-decoration: none;
  font-size: 14px;
}

.links a:hover {
  text-decoration: underline;
}
</style>
