<template>
  <div class="login-view">
    <el-card class="login-card">
      <template #header>
        <h2>ChatBI v2</h2>
      </template>

      <!-- 模式切换: 登录 / 注册 -->
      <el-radio-group v-model="mode" style="margin-bottom: 20px; width: 100%">
        <el-radio-button value="login" style="width: 50%">登录</el-radio-button>
        <el-radio-button value="register" style="width: 50%">注册</el-radio-button>
      </el-radio-group>

      <!-- 登录表单 -->
      <el-form v-if="mode === 'login'" label-position="top">
        <el-form-item label="邮箱">
          <el-input v-model="loginForm.email" placeholder="user@example.com" @keydown.enter="login" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="loginForm.password" type="password" show-password @keydown.enter="login" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="login" style="width: 100%">
            登录
          </el-button>
        </el-form-item>
      </el-form>

      <!-- 注册表单 -->
      <el-form v-else label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="registerForm.username" placeholder="昵称" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="registerForm.email" placeholder="user@example.com" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="registerForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="register" style="width: 100%">
            注册并登录
          </el-button>
        </el-form-item>
      </el-form>

      <!-- 开发登录 (DEBUG 模式折叠) -->
      <el-collapse style="margin-top: 16px">
        <el-collapse-item title="开发模式快速登录 (仅 DEBUG)" name="dev">
          <el-form label-position="top">
            <el-form-item label="租户 ID">
              <el-input v-model="devForm.tenant_id" placeholder="default_tenant" />
            </el-form-item>
            <el-form-item label="角色">
              <el-select v-model="devForm.role" style="width: 100%">
                <el-option label="管理员 (admin)" value="admin" />
                <el-option label="普通用户 (user)" value="user" />
                <el-option label="只读 (read_only)" value="read_only" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button :loading="devLoading" @click="devLogin" style="width: 100%">
                获取开发 Token
              </el-button>
            </el-form-item>
          </el-form>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth, devAuth } from '@/api'
import { extractErrorDetail } from '@/utils/error'
import { setToken } from '@/composables/useAuth'

const router = useRouter()

const mode = ref<'login' | 'register'>('login')
const loading = ref(false)
const devLoading = ref(false)

const loginForm = reactive({ email: '', password: '' })
const registerForm = reactive({ username: '', email: '', password: '' })
const devForm = reactive({
  tenant_id: 'default_tenant',
  role: 'admin' as 'admin' | 'user' | 'read_only',
})

// 正式登录 (AUTH-02)
async function login() {
  if (!loginForm.email || !loginForm.password) {
    ElMessage.warning('请输入邮箱和密码')
    return
  }
  loading.value = true
  try {
    const { data } = await auth.login({
      email: loginForm.email, password: loginForm.password,
    })
    setToken(data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    ElMessage.success('登录成功')
    router.push('/chat')
  } catch (e: any) {
    ElMessage.error('登录失败: ' + (extractErrorDetail(e)))
  } finally {
    loading.value = false
  }
}

// 注册 (AUTH-01)
async function register() {
  if (!registerForm.email || !registerForm.password || !registerForm.username) {
    ElMessage.warning('请填写完整')
    return
  }
  loading.value = true
  try {
    const { data } = await auth.register({
      email: registerForm.email,
      username: registerForm.username,
      password: registerForm.password,
    })
    setToken(data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    ElMessage.success('注册成功')
    router.push('/chat')
  } catch (e: any) {
    ElMessage.error('注册失败: ' + (extractErrorDetail(e)))
  } finally {
    loading.value = false
  }
}

// 开发登录 (仅 DEBUG)
async function devLogin() {
  devLoading.value = true
  try {
    const { data } = await devAuth.getToken({
      tenant_id: devForm.tenant_id, role: devForm.role,
    })
    setToken(data.access_token)
    ElMessage.success('登录成功')
    router.push('/chat')
  } catch (e: any) {
    ElMessage.error('登录失败: ' + (extractErrorDetail(e)))
  } finally {
    devLoading.value = false
  }
}
</script>

<style scoped>
.login-view {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.login-card {
  width: 420px;
}
.login-card h2 {
  margin: 0;
  font-size: 1.3rem;
  text-align: center;
}
</style>
