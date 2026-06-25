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
      记忆是 Agent 跨会话保留的事实和偏好。查询时会自动检索相关记忆注入上下文，帮助 Agent 记住业务约定。
    </el-alert>

    <div v-loading="loading">
      <div v-if="!memoryList.length && !loading" class="empty-hint">
        <el-empty description="暂无记忆，点击「新建记忆」添加" />
      </div>

      <el-card v-for="mem in memoryList" :key="mem.name" class="mem-card">
        <template #header>
          <div class="card-header">
            <div>
              <b>{{ mem.description || mem.name }}</b>
              <el-tag size="small" :type="typeTag(mem.type)" style="margin-left: 8px">{{ typeLabel(mem.type) }}</el-tag>
            </div>
            <div>
              <el-button size="small" :icon="Edit" @click="openEditor(mem)">编辑</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" @click="doDelete(mem)">删除</el-button>
            </div>
          </div>
        </template>
        <pre class="mem-content">{{ mem.content }}</pre>
      </el-card>
    </div>

    <!-- 编辑器抽屉 -->
    <el-drawer v-model="editorVisible" :title="editing.name ? `编辑: ${editing.name}` : '新建记忆'" size="600px">
      <el-form label-position="top">
        <el-form-item label="名称 (英文, 如 biz-convention)">
          <el-input v-model="editing.name" :disabled="!!editing.originalName" placeholder="order-convention" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editing.description" placeholder="订单业务约定" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="editing.type" style="width: 100%">
            <el-option label="项目 (project)" value="project" />
            <el-option label="用户 (user)" value="user" />
            <el-option label="反馈 (feedback)" value="feedback" />
            <el-option label="参考 (reference)" value="reference" />
          </el-select>
        </el-form-item>
        <el-form-item label="内容 (Markdown)">
          <el-input v-model="editing.content" type="textarea" :rows="16"
            placeholder="记住了哪些事实/约定/偏好..." />
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
import { Plus, Refresh, Edit, Delete } from '@element-plus/icons-vue'
import { memory as memoryApi, type Memory } from '@/api'

const memoryList = ref<Memory[]>([])
const loading = ref(false)
const editorVisible = ref(false)
const saving = ref(false)
const editing = ref<Memory & { originalName?: string }>({
  name: '', description: '', type: 'project', content: '',
})

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
.empty-hint { text-align: center; padding: 60px 0; }
</style>
