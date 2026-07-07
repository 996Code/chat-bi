<template>
  <div class="memory-view">
    <div class="page-header">
      <span class="title">Agent 记忆</span>
      <div>
        <el-button type="primary" :icon="Plus" @click="openEditor()">新建记忆</el-button>
        <el-button :icon="Refresh" @click="fetchData">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>
        <span>
          记忆是 Agent <b>跨会话保留</b> 的事实和偏好。查询时 Agent 会自动检索与问题<b>关键词相关</b>的记忆注入上下文，
          无需手动触发。描述写得越准确，召回越精准。
        </span>
      </template>
    </el-alert>

    <div v-loading="loading">
      <!-- 空白引导面板 -->
      <div v-if="!memoryList.length && !loading" class="onboarding">
        <div class="onboarding-title">🧠 还没有记忆，从常见场景快速创建：</div>
        <div class="onboarding-desc">
          记忆帮助 Agent 记住业务约定。比如"GMV 不含退款"这类规则，写一条记忆后每次相关查询都会自动召回。
        </div>
        <div class="example-cards">
          <div class="example-card" @click="createFromExample(examples[0])">
            <div class="example-icon">💡</div>
            <div class="example-name">业务约定</div>
            <div class="example-desc">记录 GMV 口径、计算规则等业务约定，查询时自动召回避免算错</div>
          </div>
          <div class="example-card" @click="createFromExample(examples[1])">
            <div class="example-icon">🏷️</div>
            <div class="example-name">字段偏好</div>
            <div class="example-desc">记录"查销量用 quantity 而不是 amount"这类字段使用偏好</div>
          </div>
          <div class="example-card" @click="createFromExample(examples[2])">
            <div class="example-icon">📊</div>
            <div class="example-name">常用维度</div>
            <div class="example-desc">记录常用的分组维度和时间范围，让 Agent 自动按习惯聚合</div>
          </div>
        </div>
      </div>

      <!-- 记忆列表 -->
      <el-card v-for="mem in memoryList" :key="mem.name" class="mem-card">
        <template #header>
          <div class="card-header">
            <div>
              <b>{{ mem.description || mem.name }}</b>
              <el-tag size="small" :type="typeTag(mem.type)" style="margin-left: 8px">{{ typeLabel(mem.type) }}</el-tag>
              <el-tag size="small" type="success" style="margin-left: 4px">自动召回</el-tag>
            </div>
            <div>
              <el-button size="small" :icon="Edit" @click="openEditor(mem)">编辑</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" @click="doDelete(mem)">删除</el-button>
            </div>
          </div>
        </template>
        <pre class="mem-content">{{ mem.content }}</pre>
        <div class="recall-hint">
          <el-icon size="12" color="#909399"><InfoFilled /></el-icon>
          <span>描述「{{ mem.description || mem.name }}」用于关键词匹配召回，越具体召回越精准</span>
        </div>
      </el-card>
    </div>

    <!-- 编辑器抽屉 -->
    <el-drawer v-model="editorVisible" :title="editing.name ? `编辑: ${editing.name}` : '新建记忆'" size="600px">
      <el-form label-position="top">
        <el-form-item label="名称 (英文, 如 biz-convention)">
          <el-input v-model="editing.name" :disabled="!!editing.originalName" placeholder="order-convention" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <span>描述 <el-tag size="small" type="warning">重要</el-tag> — 用于关键词匹配召回，越具体越精准</span>
          </template>
          <el-input v-model="editing.description" placeholder="订单业务约定：GMV计算、状态定义、时间口径" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="editing.type" style="width: 100%">
            <el-option label="项目 (project) — 业务约定和规则" value="project" />
            <el-option label="用户 (user) — 个人偏好和习惯" value="user" />
            <el-option label="反馈 (feedback) — 经验教训" value="feedback" />
            <el-option label="参考 (reference) — 外部知识" value="reference" />
          </el-select>
        </el-form-item>
        <el-form-item label="内容 (Markdown)">
          <el-input v-model="editing.content" type="textarea" :rows="16"
            placeholder="记录业务事实、约定、偏好...&#10;&#10;例如:&#10;- GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')&#10;- 退款不算入 GMV&#10;- 所有时间口径按自然月统计" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Edit, Delete, InfoFilled } from '@element-plus/icons-vue'
import { memory as memoryApi, type Memory } from '@/api'

const memoryList = ref<Memory[]>([])
const loading = ref(false)
const editorVisible = ref(false)
const saving = ref(false)
const editing = ref<Memory & { originalName?: string }>({
  name: '', description: '', type: 'project', content: '',
})

// 示例模板
const examples = [
  {
    name: 'biz-convention',
    description: '订单业务约定：GMV计算与状态定义',
    type: 'project',
    content: `## GMV 计算规则

GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')
退款不算入 GMV

## 时间口径

所有时间统计按自然月，不按30天滚动
时间字段统一使用 created_at，不用 update_time`,
  },
  {
    name: 'field-preference',
    description: '字段使用偏好：销量用quantity',
    type: 'user',
    content: `## 字段偏好

- 查"销量"时用 quantity 字段，不用 amount (amount 是金额)
- 查"用户数"时用 COUNT(DISTINCT user_id)，不用 COUNT(*) (可能含重复)
- 查"占比"时结果用百分比格式 (乘 100 + %)`,
  },
  {
    name: 'common-dimensions',
    description: '常用维度与时间范围',
    type: 'reference',
    content: `## 常用分组维度

- 按品类分组: GROUP BY category_name
- 按城市分组: GROUP BY city
- 按月份分组: DATE_TRUNC('month', created_at)

## 默认时间范围

未指定时间时默认查最近一个月的数据`,
  },
]

function createFromExample(ex: typeof examples[0]) {
  editing.value = {
    name: ex.name,
    description: ex.description,
    type: ex.type,
    content: ex.content,
    originalName: undefined,
  }
  editorVisible.value = true
}

async function fetchData() {
  loading.value = true
  try {
    const { data } = await memoryApi.list()
    memoryList.value = data
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function openEditor(mem?: Memory) {
  if (mem) {
    editing.value = { ...mem, originalName: mem.name }
  } else {
    editing.value = { name: '', description: '', type: 'project', content: '' }
  }
  editorVisible.value = true
}

async function doSave() {
  const ed = editing.value
  if (!ed.name.trim() || !ed.content.trim()) {
    ElMessage.warning('名称和内容不能为空')
    return
  }
  saving.value = true
  try {
    await memoryApi.save({
      name: ed.name, description: ed.description,
      type: ed.type, content: ed.content,
    })
    ElMessage.success('保存成功')
    editorVisible.value = false
    await fetchData()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function doDelete(mem: Memory) {
  try {
    await ElMessageBox.confirm(`确认删除记忆「${mem.description || mem.name}」?`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await memoryApi.delete(mem.name)
    ElMessage.success('已删除')
    await fetchData()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

function typeLabel(t: string): string {
  const map: Record<string, string> = { project: '项目', user: '用户', feedback: '反馈', reference: '参考' }
  return map[t] || t
}
function typeTag(t: string): any {
  const map: Record<string, any> = { project: '', user: 'success', feedback: 'warning', reference: 'info' }
  return map[t] || ''
}

onMounted(fetchData)
</script>

<style scoped>
.memory-view { padding: 24px; }
.page-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 20px;
}
.title { font-size: 1.3rem; font-weight: bold; }
.mem-card { margin-bottom: 16px; }
.card-header {
  display: flex; justify-content: space-between; align-items: center;
}
.mem-content {
  margin: 0; white-space: pre-wrap; font-size: 0.85rem;
  line-height: 1.6; color: #606266; max-height: 300px; overflow-y: auto;
}
.recall-hint {
  display: flex; align-items: center; gap: 4px;
  margin-top: 8px; font-size: 0.72rem; color: #909399;
  border-top: 1px dashed #ebeef5; padding-top: 6px;
}

/* 空白引导面板 */
.onboarding {
  text-align: center; padding: 40px 20px;
}
.onboarding-title {
  font-size: 1.1rem; font-weight: 600; color: #303133; margin-bottom: 8px;
}
.onboarding-desc {
  font-size: 0.88rem; color: #909399; margin-bottom: 24px; max-width: 520px;
  margin-left: auto; margin-right: auto; line-height: 1.6;
}
.example-cards {
  display: flex; gap: 16px; justify-content: center; flex-wrap: wrap;
  max-width: 720px; margin: 0 auto;
}
.example-card {
  flex: 1; min-width: 200px; max-width: 240px;
  border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 20px 16px; cursor: pointer;
  transition: all 0.2s; text-align: center;
}
.example-card:hover {
  border-color: #67c23a; background: #f0f9eb;
  box-shadow: 0 2px 12px rgba(103,194,58,0.1);
}
.example-icon { font-size: 1.6rem; margin-bottom: 8px; }
.example-name {
  font-size: 0.88rem; font-weight: 600; color: #303133; margin-bottom: 6px;
}
.example-desc {
  font-size: 0.76rem; color: #909399; line-height: 1.5;
}
</style>
