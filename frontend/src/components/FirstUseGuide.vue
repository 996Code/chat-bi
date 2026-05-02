<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible" class="guide-overlay">
        <div class="guide-card">
          <div class="guide-header">
            <span class="step-badge">{{ currentStep + 1 }} / {{ steps.length }}</span>
            <el-button text size="small" @click="close">跳过</el-button>
          </div>
          <div class="guide-content">
            <h3>{{ steps[currentStep].title }}</h3>
            <p>{{ steps[currentStep].description }}</p>
          </div>
          <div class="guide-footer">
            <el-button v-if="currentStep > 0" text @click="prevStep">上一步</el-button>
            <el-button v-else text @click="close">跳过</el-button>
            <el-button
              type="primary"
              @click="currentStep < steps.length - 1 ? nextStep() : complete()"
            >
              {{ currentStep < steps.length - 1 ? '下一步' : '开始使用' }}
            </el-button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

const STORAGE_KEY = 'chatbi_guide_seen'

const steps = [
  {
    title: '欢迎使用 ChatBI',
    description: '通过自然语言提问，即可从数据库中获取数据和图表。让我们开始吧！',
  },
  {
    title: '第一步：添加数据源',
    description: '点击「管理数据源」，添加你的数据库连接（MySQL / PostgreSQL）。',
  },
  {
    title: '第二步：扫描表结构',
    description: '添加数据源后点击「扫描」，系统会自动识别表结构。',
  },
  {
    title: '第三步：开始提问',
    description: '选择数据源后，在输入框中用自然语言提问，例如："上个月的销售总额是多少？"',
  },
]

const visible = ref(false)
const currentStep = ref(0)

function nextStep() {
  if (currentStep.value < steps.length - 1) {
    currentStep.value++
  }
}

function prevStep() {
  if (currentStep.value > 0) {
    currentStep.value--
  }
}

function close() {
  visible.value = false
  localStorage.setItem(STORAGE_KEY, 'true')
}

function complete() {
  visible.value = false
  localStorage.setItem(STORAGE_KEY, 'true')
  // Analytics
  import('@/api').then(({ default: api }) => {
    api.post('/analytics/event', {
      event_name: 'first_use_complete',
      event_data: {},
    }).catch(() => {})
  })
}

function show() {
  if (!localStorage.getItem(STORAGE_KEY)) {
    visible.value = true
    currentStep.value = 0
  }
}

onMounted(show)

defineExpose({ show })
</script>

<style scoped>
.guide-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.guide-card {
  background: white;
  border-radius: 16px;
  padding: 32px;
  max-width: 480px;
  width: 90%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.guide-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.step-badge {
  background: #667eea;
  color: white;
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
}

.guide-content h3 {
  margin: 0 0 12px;
  font-size: 20px;
  color: #303133;
}

.guide-content p {
  margin: 0;
  color: #606266;
  line-height: 1.6;
}

.guide-footer {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
