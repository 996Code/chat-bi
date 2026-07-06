/**
 * Excel 导出工具 — 数据 sheet + 图表 sheet (嵌 PNG)
 *
 * 对标 V1 需求:
 *   - API-05: 查询结果导出 CSV/Excel
 *   - CHART-09: 图表导出
 * 用 exceljs 生成 .xlsx, 把查询结果写进"数据"sheet,
 * ECharts 图表 getDataURL 转 PNG 嵌入"图表"sheet。
 */

import ExcelJS from 'exceljs'

export interface ExportData {
  /** 查询问题 (用作文件名) */
  question: string
  /** 列名 */
  columns: string[]
  /** 数据行 (与 columns 顺序对齐) */
  rows: Record<string, any>[]
  /** ECharts 图表实例 (可选, 有则嵌 PNG) */
  chart?: any
}

/**
 * 导出查询结果到 Excel (数据 sheet + 图表 sheet)。
 *
 * 数据 sheet: 表头加粗 + 冻结首行 + 自动列宽, CSV 注入防护 (= + - @ 开头加前缀)。
 * 图表 sheet: 把 ECharts 的 canvas 转 PNG base64 嵌入, 居中显示。
 */
export async function exportQueryToExcel(data: ExportData): Promise<void> {
  const { question, columns, rows, chart } = data
  const workbook = new ExcelJS.Workbook()
  workbook.creator = 'ChatBI'
  workbook.created = new Date()

  // ── Sheet 1: 数据 ──────────────────────────────
  const ws = workbook.addWorksheet('数据', {
    views: [{ state: 'frozen', ySplit: 1 }],
  })

  // 表头 (加粗 + 浅蓝底)
  const headerRow = ws.addRow(columns)
  headerRow.eachCell((cell) => {
    cell.font = { bold: true }
    cell.fill = {
      type: 'pattern', pattern: 'solid',
      fgColor: { argb: 'FFE8F0FE' },
    }
  })

  // 数据行 (CSV 注入防护)
  for (const row of rows) {
    ws.addRow(columns.map((col) => sanitizeCell(row[col])))
  }

  // 自动列宽 (按内容长度估算, 中文按 2 字符宽)
  ws.columns.forEach((col, i) => {
    let maxLen = String(columns[i] || '').length
    for (const row of rows) {
      const val = String(row[columns[i]] ?? '')
      const len = [...val].reduce((s, c) => s + (c.charCodeAt(0) > 127 ? 2 : 1), 0)
      if (len > maxLen) maxLen = len
    }
    col.width = Math.min(Math.max(maxLen + 2, 10), 50)
  })

  // ── Sheet 2: 图表 (嵌 PNG) ─────────────────────
  if (chart) {
    const pngBase64 = chart.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#fff',
    })
    if (pngBase64) {
      const wsChart = workbook.addWorksheet('图表')
      // base64 → ArrayBuffer (exceljs 需要 buffer)
      const base64Data = pngBase64.split(',')[1] || ''
      const buffer = base64ToBuffer(base64Data)
      // 嵌入图片 (A1 起, 留适当大小)
      const imageId = workbook.addImage({
        buffer,
        extension: 'png',
      })
      wsChart.addImage(imageId, 'A1:Q31')
    }
  }

  // ── 生成并下载 ──────────────────────────────────
  const arrayBuffer = await workbook.xlsx.writeBuffer()
  const blob = new Blob([arrayBuffer], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${question.slice(0, 20) || '查询结果'}-${formatDate()}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * CSV/Excel 注入防护: 以 = + - @ 开头的值加单引号前缀 (防 Excel 公式执行)。
 * None/undefined → 空字符串。
 */
function sanitizeCell(value: any): string {
  if (value === null || value === undefined) return ''
  const s = String(value)
  // CSV/Excel 注入: 首字符是 = + - @ 时加单引号前缀 (OWASP 推荐防护)
  if (s && ('=+-@'.includes(s[0]))) {
    return "'" + s
  }
  return s
}

/** base64 字符串 → ArrayBuffer (exceljs addImage 需要 buffer) */
function base64ToBuffer(base64: string): ArrayBuffer {
  const binaryStr = atob(base64)
  const len = binaryStr.length
  const bytes = new Uint8Array(len)
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryStr.charCodeAt(i)
  }
  return bytes.buffer
}

function formatDate(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`
}
