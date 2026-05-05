<template>
  <div class="pipeline-visual">
    <div class="pipeline-header">
      <el-icon :size="16"><Share /></el-icon>
      <span class="header-title">AI 查询流程</span>
    </div>
    <div class="pipeline-flow">
      <div
        v-for="(step, idx) in steps"
        :key="step.key"
        class="pipeline-node"
        :class="getNodeClass(step)"
      >
        <div class="node-icon">
          <el-icon v-if="step.status === 'running'" class="is-loading"><Loading /></el-icon>
          <el-icon v-else-if="step.status === 'done'" class="done-icon"><CircleCheck /></el-icon>
          <el-icon v-else-if="step.status === 'error'" class="error-icon"><CircleClose /></el-icon>
          <el-icon v-else><Clock /></el-icon>
        </div>
        <div class="node-info">
          <div class="node-name">{{ step.name }}</div>
          <div v-if="step.detail" class="node-detail">{{ step.detail }}</div>
        </div>
        <!-- Arrow connector (except last) -->
        <div v-if="idx < steps.length - 1" class="node-arrow">
          <el-icon><ArrowRight /></el-icon>
        </div>
      </div>
    </div>

    <!-- Flow diagram (visual overview) -->
    <el-dialog v-model="showDiagram" title="AI 查询流程说明" width="720px">
      <div class="flow-diagram">
        <div class="diagram-title">完整查询流程：用户提问 → 结果返回</div>
        <div class="flow-steps">
          <div class="flow-item">
            <div class="flow-circle">1</div>
            <div class="flow-label">意图识别</div>
            <div class="flow-desc">判断是否为数据查询类问题</div>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-item">
            <div class="flow-circle">2</div>
            <div class="flow-label">Schema 选择</div>
            <div class="flow-desc">LLM 两步选择：相关表 + 所需字段</div>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-item">
            <div class="flow-circle">3</div>
            <div class="flow-label">SQL 生成</div>
            <div class="flow-desc">LLM 根据问题和表结构生成 SQL</div>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-item">
            <div class="flow-circle">4</div>
            <div class="flow-label">执行查询</div>
            <div class="flow-desc">连接数据库执行，30 秒超时保护</div>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-item">
            <div class="flow-circle">5</div>
            <div class="flow-label">SQL 自愈</div>
            <div class="flow-desc">失败时自动分析错误并重试修正（最多 2 次）</div>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-item">
            <div class="flow-circle">6</div>
            <div class="flow-label">图表推断</div>
            <div class="flow-desc">根据数据特征自动选择合适的图表类型</div>
          </div>
        </div>

        <div class="diagram-note">
          <el-icon><InfoFilled /></el-icon>
          如果执行失败，流程会回到步骤 3（SQL 自愈），最多重试 2 次后返回最终结果
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Share, Loading, CircleCheck, CircleClose, Clock, ArrowRight, InfoFilled } from '@element-plus/icons-vue'

export interface PipelineStep {
  key: string
  name: string
  status: 'pending' | 'running' | 'done' | 'error'
  detail?: string
}

const props = defineProps<{
  steps: PipelineStep[]
}>()

const showDiagram = ref(false)

function getNodeClass(step: PipelineStep) {
  return `status-${step.status}`
}

function showFlowDiagram() {
  showDiagram.value = true
}

defineExpose({ showFlowDiagram })
</script>

<style scoped>
.pipeline-visual {
  padding: 12px 0;
}

.pipeline-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  color: #64748b;
  font-size: 13px;
  font-weight: 500;
}

.header-title {
  cursor: pointer;
}

.header-title:hover {
  color: #6366f1;
}

.pipeline-flow {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  overflow-x: auto;
  padding: 8px 0;
}

.pipeline-node {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding: 6px 10px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  min-width: 100px;
  transition: all 0.3s ease;
}

.pipeline-node.status-running {
  background: #eff6ff;
  border-color: #3b82f6;
}

.pipeline-node.status-done {
  background: #f0fdf4;
  border-color: #22c55e;
}

.pipeline-node.status-error {
  background: #fef2f2;
  border-color: #ef4444;
}

.pipeline-node.status-pending {
  opacity: 0.5;
}

.node-icon {
  flex-shrink: 0;
}

.done-icon {
  color: #22c55e;
}

.error-icon {
  color: #ef4444;
}

.node-info {
  min-width: 0;
}

.node-name {
  font-size: 12px;
  font-weight: 500;
  color: #334155;
  white-space: nowrap;
}

.node-detail {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 120px;
}

.node-arrow {
  flex-shrink: 0;
  color: #cbd5e1;
}

/* Flow diagram */
.flow-diagram {
  padding: 8px 0;
}

.diagram-title {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
  text-align: center;
  margin-bottom: 24px;
}

.flow-steps {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
}

.flow-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.flow-circle {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #6366f1;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
}

.flow-label {
  font-size: 13px;
  font-weight: 500;
  color: #334155;
}

.flow-desc {
  font-size: 11px;
  color: #94a3b8;
  text-align: center;
  max-width: 100px;
}

.flow-arrow {
  font-size: 20px;
  color: #cbd5e1;
  font-weight: 700;
}

.diagram-note {
  margin-top: 24px;
  padding: 10px 14px;
  background: #fffbeb;
  border-radius: 8px;
  border: 1px solid #fde68a;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #92400e;
}
</style>
