<template>
  <div class="obs-view">
    <div class="page-header">
      <el-button :icon="ArrowLeft" text @click="$router.push('/chat')">返回</el-button>
      <span class="title">系统状态</span>
    </div>
    <el-card v-loading="loading">
      <template #header><b>组件状态</b></template>
      <div v-for="(val, key) in health.components" :key="key" class="comp-row">
        <span class="comp-name">{{ key }}</span>
        <el-tag :type="val.includes('ok') || val.includes('loaded') ? 'success' : 'warning'" size="small">{{ val }}</el-tag>
      </div>
    </el-card>
    <el-card style="margin-top: 16px">
      <template #header><b>配置</b></template>
      <div v-for="(val, key) in health.config" :key="key" class="comp-row">
        <span class="comp-name">{{ key }}</span>
        <code>{{ val }}</code>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { observability } from '@/api'

const health = ref<any>({ components: {}, config: {} })
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await observability.healthDetail()
    health.value = data
  } finally { loading.value = false }
})
</script>

<style scoped>
.obs-view { padding: 24px; }
.page-header { margin-bottom: 20px; }
.title { font-size: 1.3rem; font-weight: bold; margin-left: 8px; }
.comp-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.comp-name { font-weight: 500; }
</style>
