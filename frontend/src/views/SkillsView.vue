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
      <template #title>
        <span>
          Skills 是注入 <b>SQL 生成环节</b> 的业务规则，作为约束性提示让 LLM 遵循你的业务定义。
          保存后自动热更新，下次查询即生效。按数据源类型 (MySQL/PostgreSQL) 自动匹配方言规则。
        </span>
      </template>
    </el-alert>

    <div v-loading="loading">
      <!-- 空白引导面板 -->
      <div v-if="!skillsList.length && !loading" class="onboarding">
        <div class="onboarding-title">🎯 还没有业务规则，从常见场景快速创建：</div>
        <div class="onboarding-desc">
          业务规则帮助 Agent 正确理解你的数据口径，避免把"GMV"算错、把状态值查错等常见问题。
        </div>
        <div class="example-cards">
          <div class="example-card" @click="createFromExample(examples[0])">
            <div class="example-icon">💰</div>
            <div class="example-name">GMV 计算规则</div>
            <div class="example-desc">定义 GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')，避免 Agent 遗漏已发货订单</div>
          </div>
          <div class="example-card" @click="createFromExample(examples[1])">
            <div class="example-icon">📋</div>
            <div class="example-name">状态字段枚举</div>
            <div class="example-desc">定义 status 合法值 paid/cancelled/pending/shipped，防止 Agent 用中文值查询</div>
          </div>
          <div class="example-card" @click="createFromExample(examples[2])">
            <div class="example-icon">🏷️</div>
            <div class="example-name">字段别名约定</div>
            <div class="example-desc">定义 biz_users.user_type 的含义 (1=普通/2=VIP)，让 Agent 正确解读编码字段</div>
          </div>
        </div>
      </div>

      <!-- Skill 列表 -->
      <el-card v-for="skill in skillsList" :key="skill.name" class="skill-card">
        <template #header>
          <div class="card-header">
            <div>
              <b>{{ skill.description || skill.name }}</b>
              <el-tag size="small" style="margin-left: 8px">{{ skill.name }}</el-tag>
              <el-tag size="small" type="info" style="margin-left: 4px">v{{ skill.version }}</el-tag>
              <el-tag size="small" type="warning" style="margin-left: 4px">注入 SQL 生成</el-tag>
              <el-tag v-if="skill.references && Object.keys(skill.references).length" size="small" type="success" style="margin-left: 4px">{{ Object.keys(skill.references).length }} 个方言</el-tag>
            </div>
            <div>
              <el-button size="small" :icon="Edit" @click="openEditor(skill)">编辑</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" @click="doDelete(skill)">删除</el-button>
            </div>
          </div>
        </template>
        <pre class="skill-content">{{ skill.content }}</pre>
        <!-- reference 子文件展示 -->
        <div v-if="skill.references && Object.keys(skill.references).length" class="references-section">
          <div class="references-label">方言规则 (reference) — 按数据源类型自动匹配</div>
          <el-collapse>
            <el-collapse-item v-for="(content, key) in skill.references" :key="key" :title="key + '.md'">
              <pre class="ref-content">{{ content }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>
      </el-card>
    </div>

    <!-- 编辑器抽屉 -->
    <el-drawer v-model="editorVisible" :title="editing.originalName ? `编辑: ${editing.originalName}` : '新建业务规则'" size="640px">
      <el-tabs v-model="editorTab">
        <!-- 编辑标签 -->
        <el-tab-pane label="✏️ 编辑" name="edit">
          <el-form label-position="top">
            <el-form-item label="名称 (英文, 如 gmv-rules)">
              <el-input v-model="editing.name" :disabled="!!editing.originalName" placeholder="gmv-rules" />
            </el-form-item>
            <el-form-item label="描述 (重要！用于让 Agent 理解规则的适用范围)">
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

        <!-- 预览标签 -->
        <el-tab-pane label="🔍 预览效果" name="preview">
          <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
            用当前规则对示例问题生成 SQL, 验证 LLM 是否正确理解 (不执行, 仅预览)。
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
import { extractErrorDetail } from '@/utils/error'

const skillsList = ref<Skill[]>([])
const loading = ref(false)
const editorVisible = ref(false)
const saving = ref(false)
const editing = ref<Skill & { originalName?: string }>({
  name: '', description: '', version: '1', content: '',
})
const editorTab = ref<'edit' | 'preview'>('edit')
const previewing = ref(false)
const previewQuestion = ref('各分类的销售数量统计')
const previewResult = ref<{ generated_sql: string | null; error: string | null; usage: any } | null>(null)

// 示例模板
const examples = [
  {
    name: 'gmv-rules',
    description: 'GMV 计算规则',
    version: '1',
    content: `## GMV 计算规则

GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')

注意:
- 不要遗漏 shipped 状态, 已发货也算成交
- cancelled 和 pending 状态不算 GMV
- 如果用户问"销售额""成交金额", 都按 GMV 口径计算`,
  },
  {
    name: 'status-enum',
    description: '订单状态枚举定义',
    version: '1',
    content: `## 订单状态字段规则

status 合法值 (英文, 不要用中文):
- paid: 已付款
- cancelled: 已取消
- pending: 待付款
- shipped: 已发货

禁止使用中文值 (如"已付款") 作为 WHERE 条件`,
  },
  {
    name: 'field-alias',
    description: '字段别名与编码约定',
    version: '1',
    content: `## 用户类型编码

biz_users.user_type 含义:
- 1 = 普通用户
- 2 = VIP 用户

查询"VIP用户"时应使用: WHERE user_type = 2
查询"普通用户"时应使用: WHERE user_type = 1`,
  },
]

function createFromExample(ex: typeof examples[0]) {
  editing.value = {
    name: ex.name,
    description: ex.description,
    version: ex.version,
    content: ex.content,
    originalName: undefined,
  }
  editorTab.value = 'edit'
  previewResult.value = null
  editorVisible.value = true
}

async function fetchData() {
  loading.value = true
  try {
    const { data } = await skillsApi.list()
    skillsList.value = data
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (extractErrorDetail(e)))
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
  previewResult.value = null
  editorVisible.value = true
}

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
      error: extractErrorDetail(e) || '预览失败',
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
    ElMessage.error('保存失败: ' + (extractErrorDetail(e)))
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
    ElMessage.error('删除失败: ' + (extractErrorDetail(e)))
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
.references-section { margin-top: 12px; border-top: 1px dashed #dcdfe6; padding-top: 12px; }
.references-label { font-size: 0.8rem; color: #909399; margin-bottom: 6px; }
.ref-content {
  margin: 0; white-space: pre-wrap; font-size: 0.82rem;
  line-height: 1.5; color: #606266; max-height: 200px; overflow-y: auto;
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
  border-color: #667eea; background: #f5f7ff;
  box-shadow: 0 2px 12px rgba(102,126,234,0.1);
}
.example-icon { font-size: 1.6rem; margin-bottom: 8px; }
.example-name {
  font-size: 0.88rem; font-weight: 600; color: #303133; margin-bottom: 6px;
}
.example-desc {
  font-size: 0.76rem; color: #909399; line-height: 1.5;
}

/* 预览结果 */
.preview-result { margin-top: 16px; }
.preview-label { font-size: 0.8rem; color: #909399; margin-bottom: 6px; }
.preview-sql {
  background: #f5f7fa; border-radius: 6px; padding: 10px 14px;
  font-size: 0.82rem; white-space: pre-wrap; word-break: break-all;
  max-height: 240px; overflow-y: auto; margin: 0;
}
.preview-usage { font-size: 0.72rem; color: #c0c4cc; margin-top: 6px; }
</style>
