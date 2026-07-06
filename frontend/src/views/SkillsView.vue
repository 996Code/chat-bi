<template>
  <div class="skills-view">
    <div class="page-header">
      <span class="title">业务规则 (Skills)</span>
      <div>
        <el-button type="primary" :icon="Plus" @click="openEditor()">新建规则</el-button>
        <el-button :icon="Refresh" @click="fetchData">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      Skills 是注入 SQL 生成 prompt 的业务规则（如 GMV 定义、状态枚举）。保存后自动热更新，下次查询即生效。
    </el-alert>

    <div v-loading="loading">
      <div v-if="!skillsList.length && !loading" class="empty-hint">
        <el-empty description="暂无业务规则，点击「新建规则」添加" />
      </div>

      <el-card v-for="skill in skillsList" :key="skill.name" class="skill-card">
        <template #header>
          <div class="card-header">
            <div>
              <b>{{ skill.description || skill.name }}</b>
              <el-tag size="small" style="margin-left: 8px">{{ skill.name }}</el-tag>
              <el-tag size="small" type="info" style="margin-left: 4px">v{{ skill.version }}</el-tag>
              <el-tag v-if="skill.references && Object.keys(skill.references).length" size="small" type="success" style="margin-left: 4px">{{ Object.keys(skill.references).length }} 个参考</el-tag>
            </div>
            <div>
              <el-button size="small" :icon="Edit" @click="openEditor(skill)">编辑</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" @click="doDelete(skill)">删除</el-button>
            </div>
          </div>
        </template>
        <pre class="skill-content">{{ skill.content }}</pre>
        <!-- T060: reference 子文件展示 -->
        <div v-if="skill.references && Object.keys(skill.references).length" class="references-section">
          <div class="references-label">参考规则 (reference)</div>
          <el-collapse>
            <el-collapse-item v-for="(content, key) in skill.references" :key="key" :title="key + '.md'">
              <pre class="ref-content">{{ content }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>
      </el-card>
    </div>

    <!-- 编辑器抽屉 (T041: 编辑 + 预览双标签) -->
    <el-drawer v-model="editorVisible" :title="editing.originalName ? `编辑: ${editing.originalName}` : '新建业务规则'" size="640px">
      <el-tabs v-model="editorTab">
        <!-- 编辑标签 -->
        <el-tab-pane label="✏️ 编辑" name="edit">
          <el-form label-position="top">
            <el-form-item label="名称 (英文, 如 sql-rules)">
              <el-input v-model="editing.name" :disabled="!!editing.originalName" placeholder="gmv-rules" />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="editing.description" placeholder="GMV 计算规则与订单状态定义" />
            </el-form-item>
            <el-form-item label="版本">
              <el-input v-model="editing.version" placeholder="1" />
            </el-form-item>
            <el-form-item label="规则内容 (Markdown)">
              <el-input v-model="editing.content" type="textarea" :rows="16"
                placeholder="订单状态枚举:&#10;- paid: 已付款&#10;- cancelled: 已取消&#10;- pending: 待付款&#10;- shipped: 已发货&#10;&#10;GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')" />
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- T041: 预览标签 — 用规则对示例问题跑一次 SQL 生成 -->
        <el-tab-pane label="🔍 预览效果" name="preview">
          <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
            用当前规则内容对示例问题生成 SQL, 验证规则是否被 LLM 正确理解 (不执行, 仅预览)。
          </el-alert>
          <el-form label-position="top">
            <el-form-item label="测试问题">
              <el-input v-model="previewQuestion" placeholder="各分类的销售数量统计" />
            </el-form-item>
            <el-button type="primary" :loading="previewing" :disabled="!editing.content.trim()"
              @click="doPreview">运行预览</el-button>
          </el-form>

          <div v-if="previewResult" class="preview-result">
            <div class="preview-label">生成的 SQL</div>
            <pre class="preview-sql">{{ previewResult.generated_sql || '(空)' }}</pre>
            <div v-if="previewResult.usage" class="preview-usage">
              {{ previewResult.usage.total_tokens }} tokens
            </div>
            <el-alert v-if="previewResult.error" type="warning" :closable="false" :title="previewResult.error" style="margin-top: 8px" />
          </div>
        </el-tab-pane>
      </el-tabs>

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
import { skills as skillsApi, type Skill } from '@/api'

const skillsList = ref<Skill[]>([])
const loading = ref(false)
const editorVisible = ref(false)
const saving = ref(false)
const editing = ref<Skill & { originalName?: string }>({
  name: '', description: '', version: '1', content: '',
})
// T041: 预览功能
const editorTab = ref<'edit' | 'preview'>('edit')
const previewing = ref(false)
const previewQuestion = ref('各分类的销售数量统计')
const previewResult = ref<{ generated_sql: string | null; error: string | null; usage: any } | null>(null)

async function fetchData() {
  loading.value = true
  try {
    const { data } = await skillsApi.list()
    skillsList.value = data
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function openEditor(skill?: Skill) {
  if (skill) {
    editing.value = { ...skill, originalName: skill.name }
  } else {
    editing.value = { name: '', description: '', version: '1', content: '' }
  }
  editorTab.value = 'edit'
  previewResult.value = null  // 重置预览结果
  editorVisible.value = true
}

// T041: 预览规则效果
async function doPreview() {
  if (!editing.value.content.trim()) return
  previewing.value = true
  previewResult.value = null
  try {
    const { data } = await skillsApi.preview(editing.value.content, previewQuestion.value)
    previewResult.value = data
  } catch (e: any) {
    previewResult.value = {
      generated_sql: null,
      error: e.response?.data?.detail || e.message || '预览失败',
      usage: null,
    }
  } finally {
    previewing.value = false
  }
}

async function doSave() {
  const ed = editing.value
  if (!ed.name.trim() || !ed.content.trim()) {
    ElMessage.warning('名称和内容不能为空')
    return
  }
  saving.value = true
  try {
    await skillsApi.save({
      name: ed.name, description: ed.description,
      version: ed.version, content: ed.content,
      references: ed.references,
    })
    ElMessage.success('保存成功 (热更新已生效)')
    editorVisible.value = false
    await fetchData()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

async function doDelete(skill: Skill) {
  try {
    await ElMessageBox.confirm(`确认删除规则「${skill.description || skill.name}」?`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await skillsApi.delete(skill.name)
    ElMessage.success('已删除')
    await fetchData()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
  }
}

onMounted(fetchData)
</script>

<style scoped>
.skills-view { padding: 24px; }
.page-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 20px;
}
.title { font-size: 1.3rem; font-weight: bold; }
.skill-card { margin-bottom: 16px; }
.card-header {
  display: flex; justify-content: space-between; align-items: center;
}
.skill-content {
  margin: 0; white-space: pre-wrap; font-size: 0.85rem;
  line-height: 1.6; color: #606266; max-height: 300px; overflow-y: auto;
}
/* T060: reference 子文件样式 */
.references-section { margin-top: 12px; border-top: 1px dashed #dcdfe6; padding-top: 12px; }
.references-label { font-size: 0.8rem; color: #909399; margin-bottom: 6px; }
.ref-content {
  margin: 0; white-space: pre-wrap; font-size: 0.82rem;
  line-height: 1.5; color: #606266; max-height: 200px; overflow-y: auto;
}
.empty-hint { text-align: center; padding: 60px 0; }
/* T041: 预览结果 */
.preview-result { margin-top: 16px; }
.preview-label { font-size: 0.8rem; color: #909399; margin-bottom: 6px; }
.preview-sql {
  background: #f5f7fa; border-radius: 6px; padding: 10px 14px;
  font-size: 0.82rem; white-space: pre-wrap; word-break: break-all;
  max-height: 240px; overflow-y: auto; margin: 0;
}
.preview-usage { font-size: 0.72rem; color: #c0c4cc; margin-top: 6px; }
</style>
