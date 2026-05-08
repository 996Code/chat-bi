<template>
  <div ref="containerRef" class="chart-container">
    <!-- 指标卡 -->
    <div v-if="chartType === 'metric'" class="metric-card">
      <div class="metric-value">{{ metricValue }}</div>
      <div class="metric-label">{{ columns[0] }}</div>
    </div>
    <!-- ECharts 图表 -->
    <div v-else-if="chartType !== 'table'" ref="chartRef" class="echarts-wrapper" :style="{ height: chartHeight + 'px' }"></div>
    <!-- 表格 -->
    <el-table v-else :data="rows" border size="small" :max-height="tableMaxHeight">
      <el-table-column v-for="col in columns" :key="col" :prop="col" :label="col" />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, computed, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = withDefaults(defineProps<{
  chartType: string
  columns: string[]
  rows: Record<string, any>[]
  chartHeight?: number
}>(), {
  chartHeight: 300,
})

const emit = defineEmits<{}>()

const currentType = ref(props.chartType)

watch(() => props.chartType, (val) => {
  currentType.value = val
})

const chartRef = ref<HTMLElement>()
const containerRef = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

const metricValue = computed(() => {
  if (props.rows.length > 0 && props.columns.length > 0) {
    const val = props.rows[0][props.columns[0]]
    return typeof val === 'number' ? val.toLocaleString() : val
  }
  return '-'
})

const tableMaxHeight = ref(300)

function updateTableHeight() {
  if (props.chartType !== 'table') return
  // Use chartHeight prop as a hint for table max height too
  tableMaxHeight.value = props.chartHeight
}

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
  // Ensure ECharts picks up the current container size
  chart.resize()
}

function handleResize() {
  chart?.resize()
}

watch(() => [props.chartType, props.rows, props.columns, props.chartHeight], () => {
  renderChart()
  updateTableHeight()
}, { deep: true })

onMounted(() => {
  renderChart()
  updateTableHeight()
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
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.metric-card {
  text-align: center;
  padding: 12px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 8px;
  color: white;
}

.metric-value {
  font-size: 28px;
  font-weight: 700;
}

.metric-label {
  font-size: 12px;
  opacity: 0.85;
  margin-top: 2px;
}

.echarts-wrapper {
  width: 100%;
}
</style>