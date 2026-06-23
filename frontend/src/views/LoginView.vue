<template>
  <div class="login-view">
    <el-card class="login-card">
      <template #header>
        <h2>ChatBI v2 — 开发登录</h2>
      </template>

      <el-alert
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        开发模式 (DEBUG=True)。正式登录功能在 Phase 4 实现。
      </el-alert>

      <el-form label-position="top">
        <el-form-item label="租户 ID">
          <el-input v-model="form.tenant_id" placeholder="default_tenant" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option label="管理员 (admin)" value="admin" />
            <el-option label="普通用户 (user)" value="user" />
            <el-option label="只读 (read_only)" value="read_only" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="login" style="width: 100%">
            获取开发 Token 并进入
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { devAuth } from '@/api'

const router = useRouter()
const loading = ref(false)

const form = reactive({
  tenant_id: 'default_tenant',
  role: 'admin' as 'admin' | 'user' | 'read_only',
})

async function login() {
  loading.value = true
  try {
    const { data } = await devAuth.getToken({
      tenant_id: form.tenant_id,
      role: form.role,
    })
    localStorage.setItem('access_token', data.access_token)
    ElMessage.success('登录成功')
    router.push('/datasources')
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    ElMessage.error('登录失败: ' + msg)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-view {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: #f5f7fa;
}
.login-card {
  width: 420px;
}
.login-card h2 {
  margin: 0;
  font-size: 1.2rem;
}
</style>
