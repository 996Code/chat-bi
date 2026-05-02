<template>
  <div class="login-page">
    <div class="login-card">
      <h1 class="title">注册账号</h1>
      <p class="subtitle">创建你的 ChatBI 账号</p>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="0" @submit.prevent="handleRegister">
        <el-form-item prop="email">
          <el-input v-model="form.email" placeholder="邮箱地址" prefix-icon="Message" size="large" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码（至少8位，包含字母和数字）" prefix-icon="Lock" size="large" show-password />
        </el-form-item>
        <el-form-item prop="confirmPassword">
          <el-input v-model="form.confirmPassword" type="password" placeholder="确认密码" prefix-icon="Lock" size="large" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" style="width: 100%" :loading="authStore.loading" native-type="submit">
            注册
          </el-button>
        </el-form-item>
      </el-form>
      <div class="links">
        <router-link to="/login">已有账号？去登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'
import type { FormInstance } from 'element-plus'
import { ElMessage } from 'element-plus'

const router = useRouter()
const authStore = useAuthStore()
const formRef = ref<FormInstance>()

const form = reactive({
  email: '',
  password: '',
  confirmPassword: '',
})

const validateConfirm = (_rule: any, value: string, callback: any) => {
  if (value !== form.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const rules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }, { type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少8个字符', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    { validator: validateConfirm, trigger: 'blur' },
  ],
}

async function handleRegister() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  const result = await authStore.register(form.email, form.password)
  if (result === true) {
    ElMessage.success('注册成功，请登录')
    router.push('/login')
  } else {
    ElMessage.error(result as string)
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
