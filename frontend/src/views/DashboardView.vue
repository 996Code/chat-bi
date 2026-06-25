<template>
  <div class="dash-view">
    <div class="page-header">
      <el-button :icon="ArrowLeft" text @click="$router.push('/chat')">返回</el-button>
      <span class="title">看板</span>
      <!-- 导出用数据源 (SavedQuery 无 data_source_id, 需选择) -->
      <el-select
        v-model="exportDsId" placeholder="导出数据源" size="small"
        style="width: 200px; margin-left: auto"
      >
        <el-option v-for="ds in dataSources" :key="ds.id" :label="ds.name" :value="ds.id" />
      </el-select>
      <el-button :icon="Refresh" text :loading="loading" @click="loadQueries">刷新</el-button>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!queries.length && !loading" description="还没有保存的查询, 去对话页查询后会自动保存">
        <el-button type="primary" @click="$router.push('/chat')">去提问</el-button>
      </el-empty>

      <div v-else class="grid">
        <el-card
          v-for="q in queries" :key="q.id"
          class="card" shadow="hover"
          @click="toggleCard(q.id)"
        >
          <div class="card-q">{{ q.question }}</div>
          <!-- 图表 -->
          <div
            v-if="q.chart_config"
            :ref="(el: any) => setChartRef(el, q.id)"
            class="card-chart"
          ></div>
          <el-empty v-else description="无图表配置" :image-size="40" />

          <!-- 展开: SQL + 元信息 -->
          <transition name="el-zoom-in-top">
            <div v-if="openIds[q.id]" class="card-detail">
              <div class="meta-row">
                <el-tag size="small">{{ formatTime(q.created_at) }}</el-tag>
                <el-tag v-if="q.conversation_id" size="small" type="info" @click.stop="goConversation(q.conversation_id!)">来源对话</el-tag>
              </div>
              <div class="sql-box">
                <div class="sql-label">SQL</div>
                <pre>{{ q.sql_text }}</pre>
              </div>
              <!-- UX-08: CSV 导出 (需先选数据源) -->
              <el-button
                size="small" :icon="Download" plain
                :disabled="!exportDsId" :loading="exportingId === q.id"
                @click.stop="exportCsv(q)"
              >
                导出 CSV
              </el-button>
            </div>
          </transition>
          <div class="card-footer">点击卡片{{ openIds[q.id] ? '收起' : '展开' }}详情</div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh, Download } from '@element-plus/icons-vue'
import { savedQuery, datasource, type SavedQuery } from '@/api'
import * as echarts from 'echarts'

const router = useRouter()
const queries = ref<SavedQuery[]>([])
const loading = ref(false)
const openIds = reactive<Record<string, boolean>>({})
const chartRefs: Record<string, HTMLElement> = {}
const chartInstances: Record<string, echarts.ECharts> = {}
// UX-08: CSV 导出
const dataSources = ref<{ id: string; name: string }[]>([])
const exportDsId = ref('')
const exportingId = ref<string | null>(null)

function setChartRef(el: any, id: string) {
  if (el) chartRefs[id] = el
}

async function loadQueries() {
  loading.value = true
  try {
    const { data } = await savedQuery.list()
    queries.value = data
    await nextTick()
    // 渲染所有图表
    for (const q of queries.value) {
      if (q.chart_config && chartRefs[q.id]) {
        const chart = echarts.init(chartRefs[q.id])
        chart.setOption(q.chart_config)
        chartInstances[q.id] = chart
      }
    }
  } catch {
    // fail-closed: 错误已在拦截器提示
  } finally {
    loading.value = false
  }
}

function toggleCard(id: string) {
  openIds[id] = !openIds[id]
}

function goConversation(convId: string) {
  // 跳到对话页 (ChatView 通过路由 query 或侧边栏加载历史)
  router.push({ path: '/chat', query: { conv: convId } })
}

function formatTime(ts: string | null): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return ''
  }
}

// UX-08: 加载数据源供导出选择
async function loadDataSources() {
  try {
    const { data } = await datasource.list()
    dataSources.value = data.map((d) => ({ id: d.id, name: d.name }))
    if (dataSources.value.length && !exportDsId.value) {
      exportDsId.value = dataSources.value[0].id
    }
  } catch {
    // fail-closed: 不阻塞看板
  }
}

// UX-08: 导出 CSV (Blob 下载)
async function exportCsv(q: SavedQuery) {
  if (!exportDsId.value) {
    ElMessage.warning('请先选择导出数据源')
    return
  }
  exportingId.value = q.id
  try {
    const res = await savedQuery.exportCsv(q.id, exportDsId.value)
    const blob = new Blob([res.data], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `query-${q.id.slice(0, 8)}.csv`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出 CSV')
  } catch (e: any) {
    // fail-closed: responseType=blob 时错误响应也是 blob, 需转成 JSON 读 detail
    let detail = e.message || '查询执行失败'
    if (e.response?.data instanceof Blob) {
      try {
        const text = await e.response.data.text()
        detail = JSON.parse(text).detail || detail
      } catch { /* 解析失败用默认 */ }
    } else if (e.response?.data?.detail) {
      detail = e.response.data.detail
    }
    ElMessage.error('导出失败: ' + detail)
  } finally {
    exportingId.value = null
  }
}

onMounted(() => {
  loadQueries()
  loadDataSources()
})
</script>

<style scoped>
.dash-view { padding: 24px; }
.page-header { display: flex; align-items: center; gap: 8px; margin-bottom: 20px; }
.title { font-size: 1.3rem; font-weight: bold; flex: 1; margin-left: 8px; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}
.card { cursor: pointer; transition: transform 0.15s; }
.card:hover { transform: translateY(-2px); }
.card-q { font-size: 0.9rem; font-weight: 600; color: #303133; margin-bottom: 10px; line-height: 1.4; }
.card-chart { width: 100%; height: 220px; }

.card-detail { margin-top: 10px; padding-top: 10px; border-top: 1px solid #f0f0f0; }
.meta-row { display: flex; gap: 6px; margin-bottom: 8px; }
.sql-box { background: #f5f7fa; border-radius: 6px; padding: 8px 10px; }
.sql-label { font-size: 0.7rem; color: #909399; margin-bottom: 4px; }
.sql-box pre { margin: 0; font-size: 0.75rem; white-space: pre-wrap; word-break: break-all; max-height: 120px; overflow-y: auto; }
.card-footer { margin-top: 8px; font-size: 0.72rem; color: #c0c4cc; text-align: center; }
</style>
