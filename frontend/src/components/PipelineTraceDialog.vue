<template>
  <el-dialog v-model="visible" :title="dialogTitle" width="800px">
    <!-- Per-query trace (from message button) -->
    <div v-if="steps.length > 0" class="trace-table">
      <div class="trace-header">
        <span>{{ dialogTitle }}</span>
      </div>
      <!-- Optional query info header (for slow query trace from monitoring) -->
      <div v-if="queryInfo" class="trace-info-header">
        <div class="trace-info-line" v-if="queryInfo.datasource"><b>数据源：</b>{{ queryInfo.datasource }}</div>
        <div class="trace-info-line" v-if="queryInfo.question"><b>问题：</b>{{ queryInfo.question }}</div>
        <div class="trace-info-line" v-if="queryInfo.totalTime">
          <b>总耗时：</b>{{ queryInfo.totalTime }}ms
          <span v-if="queryInfo.sqlTime" class="trace-sql-time">（SQL 执行：{{ queryInfo.sqlTime }}ms）</span>
        </div>
        <div class="trace-info-line" v-if="queryInfo.status !== undefined">
          <b>状态：</b>
          <el-tag v-if="queryInfo.status === 'error'" type="danger" size="small">错误</el-tag>
          <el-tag v-else type="success" size="small">成功</el-tag>
          <span v-if="queryInfo.error" class="trace-error">{{ queryInfo.error }}</span>
        </div>
        <!-- Custom action slot (conversation link, eval badge, etc.) -->
        <div class="trace-info-line" v-if="$slots.action">
          <slot name="action"></slot>
        </div>
      </div>
      <el-table :data="steps" stripe size="small" style="width: 100%">
        <el-table-column label="#" width="40" align="center">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="步骤" width="120" prop="label" />
        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'done'" type="success" size="small">完成</el-tag>
            <el-tag v-else-if="row.status === 'failed'" type="danger" size="small">失败</el-tag>
            <el-tag v-else type="info" size="small">进行中</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="80" align="center">
          <template #default="{ row }">
            <span v-if="row.duration_ms">{{ row.duration_ms }}ms</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="执行详情" min-width="350">
          <template #default="{ row }">
            <!-- Cache hit step -->
            <div v-if="row.cached" class="trace-detail-block">
              <el-tag :type="row.cache_type === 'semantic' ? 'warning' : 'success'" size="small">
                {{ row.cache_type === 'semantic' ? '语义缓存' : '精确缓存' }}
              </el-tag>
              <span class="cache-hit-text">{{ row.detail }}</span>
            </div>
            <!-- Cache miss step -->
            <div v-else-if="row.label === '缓存检查'" class="trace-detail-block">
              <el-tag type="info" size="small">未命中</el-tag>
              <span class="cache-miss-text">{{ row.detail }}</span>
            </div>
            <!-- Slow query step -->
            <div v-else-if="row.is_slow" class="trace-detail-block">
              <el-tag type="danger" size="small">慢查询</el-tag>
              <span class="slow-query-text">{{ row.detail }}</span>
            </div>
            <!-- Schema selection: tables + columns -->
            <div v-else-if="row.type === 'semantics' && row.tables" class="trace-detail-block">
              <div class="trace-detail-line">{{ row.detail }}</div>
              <div v-for="t in row.tables" :key="t" class="trace-detail-sub">
                <span class="trace-table-name">{{ t }}</span>
                <span v-if="row.columns?.[t]?.length" class="trace-col-list">
                  字段: {{ row.columns[t].join(', ') }}
                </span>
              </div>
            </div>
            <!-- SQL generation: full SQL + validation + attempt -->
            <div v-else-if="row.type === 'sql'" class="trace-detail-block">
              <div v-if="row.sql" class="trace-sql"><pre>{{ row.sql }}</pre></div>
              <div v-if="row.attempt && row.attempt > 1" class="trace-detail-sub">
                <el-tag size="small" type="warning">第 {{ row.attempt }} 次尝试</el-tag>
              </div>
              <div v-if="row.validation" class="trace-detail-sub">
                <div v-if="row.validation.table_fixes?.length">
                  表名修复: {{ row.validation.table_fixes.join(', ') }}
                </div>
                <div v-if="row.validation.column_fixes?.length">
                  列名修复: {{ row.validation.column_fixes.join(', ') }}
                </div>
              </div>
              <div v-if="row.error_code" class="trace-detail-sub">
                <el-tag size="small" type="danger">错误码: {{ row.error_code }}</el-tag>
                <span v-if="row.retry">第 {{ row.retry }} 次自愈</span>
              </div>
              <div v-if="!row.sql && row.detail">{{ row.detail }}</div>
            </div>
            <!-- Data execution -->
            <div v-else-if="row.type === 'data'" class="trace-detail-block">
              <div>{{ row.detail }}</div>
            </div>
            <!-- Other steps -->
            <span v-else>{{ row.detail || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <!-- Static flow description (from header button) -->
    <div v-else class="flow-desc">
      <div class="flow-step-list">
        <div class="flow-step-item">
          <div class="flow-step-num">1</div>
          <div class="flow-step-body">
            <div class="flow-step-title">意图识别</div>
            <div class="flow-step-text">判断用户问题是否为数据查询意图</div>
          </div>
        </div>
        <div class="flow-step-item">
          <div class="flow-step-num">2</div>
          <div class="flow-step-body">
            <div class="flow-step-title">Schema 选择</div>
            <div class="flow-step-text">LLM 两步选择：先选相关表，再选相关列，构建精简 Schema 上下文</div>
          </div>
        </div>
        <div class="flow-step-item">
          <div class="flow-step-num">3</div>
          <div class="flow-step-body">
            <div class="flow-step-title">SQL 生成</div>
            <div class="flow-step-text">基于 Schema 上下文和用户问题，生成 SQL 查询语句</div>
          </div>
        </div>
        <div class="flow-step-item">
          <div class="flow-step-num">4</div>
          <div class="flow-step-body">
            <div class="flow-step-title">执行查询</div>
            <div class="flow-step-text">在数据源上执行生成的 SQL，返回查询结果</div>
          </div>
        </div>
        <div class="flow-step-item">
          <div class="flow-step-num">5</div>
          <div class="flow-step-body">
            <div class="flow-step-title">SQL 自愈（失败时）</div>
            <div class="flow-step-text">若执行失败，LLM 分析错误原因并修正 SQL，最多重试 2 轮</div>
          </div>
        </div>
        <div class="flow-step-item">
          <div class="flow-step-num">6</div>
          <div class="flow-step-body">
            <div class="flow-step-title">图表推断</div>
            <div class="flow-step-text">根据返回的列名和数据特征，推荐最佳可视化图表类型</div>
          </div>
        </div>
      </div>
      <div class="flow-note">
        点击查询结果卡片上的「完整流程」按钮，可查看该次查询的实际执行记录。
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PipelineStep } from '@/stores/chatStore'

const props = defineProps<{
  modelValue: boolean
  steps: PipelineStep[]
  title?: string
  queryInfo?: { datasource?: string; question?: string; totalTime?: number; sqlTime?: number; status?: 'success' | 'error'; error?: string }
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const dialogTitle = computed(() => props.title || (props.steps.length > 0 ? '查询执行记录' : '查询流程说明'))
</script>

<style scoped>
.trace-table {
  padding: 8px 0;
}

.trace-header {
  margin-bottom: 12px;
  font-size: 14px;
  color: #606266;
}

.trace-sql pre {
  margin: 0;
  padding: 6px 10px;
  background: #1e1e1e;
  color: #a5d6ff;
  font-size: 12px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
}

.trace-detail-block {
  line-height: 1.5;
}

.trace-detail-line {
  color: #303133;
  font-size: 13px;
}

.trace-detail-sub {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

.trace-table-name {
  font-weight: 600;
  color: #409eff;
}

.trace-col-list {
  color: #909399;
  margin-left: 4px;
}

.cache-hit-text {
  color: #67c23a;
  margin-left: 8px;
  font-size: 13px;
}

.cache-miss-text {
  color: #909399;
  margin-left: 8px;
  font-size: 13px;
}

.slow-query-text {
  color: #f56c6c;
  margin-left: 8px;
  font-size: 13px;
}

/* Static flow description */
.flow-step-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.flow-step-item {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.flow-step-num {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #6366f1;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
}

.flow-step-body {
  flex: 1;
  padding-top: 4px;
}

.flow-step-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.flow-step-text {
  font-size: 13px;
  color: #909399;
  margin-top: 2px;
  line-height: 1.5;
}

.flow-note {
  margin-top: 20px;
  padding: 10px 14px;
  background: #f0f5ff;
  border-radius: 8px;
  border: 1px solid #d0e0ff;
  font-size: 13px;
  color: #409eff;
}

/* Query info header (for slow query trace) */
.trace-info-header {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
}
.trace-info-line {
  font-size: 13px;
  color: #303133;
  margin-bottom: 4px;
}
.trace-info-line:last-child {
  margin-bottom: 0;
}
.trace-sql-time {
  color: #e6a23c;
  font-weight: 500;
}
.trace-error {
  color: #f56c6c;
  font-size: 12px;
  margin-left: 8px;
}
</style>
