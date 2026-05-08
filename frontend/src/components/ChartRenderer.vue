<template>
  <div class="chart-container">
    <!-- Chart type selector -->
    <div class="chart-toolbar">
      <el-select v-model="currentType" size="small" style="width: 120px" @change="onChartTypeChange">
        <el-option label="指标卡" value="metric" />
        <el-option label="折线图" value="line" />
        <el-option label="柱状图" value="bar" />
        <el-option label="饼图" value="pie" />
        <el-option label="散点图" value="scatter" />
        <el-option label="表格" value="table" />
      </el-select>
    </div>
    <!-- 指标卡 -->
    <div v-if="chartType === 'metric'" class="metric-card">
      <div class="metric-value">{{ metricValue }}</div>
      <div class="metric-label">{{ columns[0] }}</div>
    </div>
    <!-- ECharts 图表 -->
    <div v-else-if="chartType !== 'table'" ref="chartRef" class="echarts-wrapper" :style="{ height: chartHeight + 'px' }"></div>
    <!-- 表格 -->
    <el-table v-else :data="rows" border size="small" max-height="400">
      <el-table-column v-for="col in columns" :key="col" :prop="col" :label="col" />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, computed, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{
  chartType: string
  columns: string[]
  rows: Record<string, any>[]
}>()

const emit = defineEmits<{
  'chart-type-change': [type: string]
}>()

const currentType = ref(props.chartType)

watch(() => props.chartType, (val) => {
  currentType.value = val
})

const chartRef = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

const chartHeight = computed(() => {
  const ct = currentType.value
  const n = props.rows.length
  if (ct === 'pie') return Math.max(300, Math.min(500, n * 30 + 200))
  if (ct === 'bar' || ct === 'line') return Math.max(280, Math.min(600, n * 28 + 100))
  return 320
})

const metricValue = computed(() => {
  if (props.rows.length > 0 && props.columns.length > 0) {
    const val = props.rows[0][props.columns[0]]
    return typeof val === 'number' ? val.toLocaleString() : val
  }
  return '-'
})

function getOption(): echarts.EChartsOption {
  const { columns, rows } = props
  const chartType = currentType.value

  if (chartType === 'line' && columns.length >= 2) {
    const xCol = columns[0]
    const yCol = columns[1]
    return {
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: rows.map(r => String(r[xCol])), axisLabel: { rotate: 30 } },
      yAxis: { type: 'value' },
      series: [{ name: yCol, type: 'line', data: rows.map(r => Number(r[yCol] ?? 0)), smooth: true }],
      grid: { left: 60, right: 20, bottom: 50, top: 30, containLabel: true },
    }
  }

  if (chartType === 'bar' && columns.length >= 2) {
    const xCol = columns[0]
    const yCol = columns[1]
    return {
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: rows.map(r => String(r[xCol])), axisLabel: { rotate: 30 } },
      yAxis: { type: 'value' },
      series: [{ name: yCol, type: 'bar', data: rows.map(r => Number(r[yCol] ?? 0)) }],
      grid: { left: 60, right: 20, bottom: 50, top: 30, containLabel: true },
    }
  }

  if (chartType === 'pie' && columns.length >= 2) {
    const nameCol = columns[0]
    const valCol = columns[1]
    return {
      tooltip: { trigger: 'item' },
      series: [{
        type: 'pie',
        radius: ['35%', '65%'],
        center: ['50%', '50%'],
        data: rows.map(r => ({ name: String(r[nameCol]), value: Number(r[valCol] ?? 0) })),
        label: { formatter: '{b}: {d}%' },
      }],
    }
  }

  if (chartType === 'scatter' && columns.length >= 2) {
    const xCol = columns[0]
    const yCol = columns[1]
    return {
      tooltip: { trigger: 'item' },
      xAxis: { type: 'value', name: xCol },
      yAxis: { type: 'value', name: yCol },
      series: [{
        type: 'scatter',
        data: rows.map(r => [Number(r[xCol] ?? 0), Number(r[yCol] ?? 0)]),
      }],
      grid: { left: 60, right: 20, bottom: 50, top: 30, containLabel: true },
    }
  }

  return {}
}

async function renderChart() {
  if (currentType.value === 'metric' || currentType.value === 'table') return
  await nextTick()
  if (!chartRef.value) return

  if (!chart) {
    chart = echarts.init(chartRef.value)
  }
  const option = getOption()
  if (Object.keys(option).length > 0) {
    chart.setOption(option, true)
  }
}

function onChartTypeChange() {
  emit('chart-type-change', currentType.value)
  if (currentType.value === 'table' || currentType.value === 'metric') {
    chart?.dispose()
    chart = null
  } else {
    renderChart()
  }
}

function handleResize() {
  chart?.resize()
}

watch(() => [props.chartType, props.rows, props.columns], () => {
  renderChart()
}, { deep: true })

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
})

function getChartDataURL(): string | null {
  if (!chart) return null
  return chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
}

defineExpose({ getChartDataURL })
</script>

<style scoped>
.chart-container {
  margin-top: 8px;
}

.chart-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.metric-card {
  text-align: center;
  padding: 24px 32px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  color: white;
}

.metric-value {
  font-size: 36px;
  font-weight: 700;
}

.metric-label {
  font-size: 14px;
  opacity: 0.85;
  margin-top: 4px;
}

.echarts-wrapper {
  width: 100%;
}
</style>
