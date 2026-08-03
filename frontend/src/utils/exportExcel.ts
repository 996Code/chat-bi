/**
 * Excel 导出工具 — 数据 sheet + 图表 sheet (嵌 PNG)
 *
 * 对标 V1 需求:
 *   - API-05: 查询结果导出 CSV/Excel
 *   - CHART-09: 图表导出
 *
 * 架构职责:
 *   将查询结果和 ECharts 图表导出为 .xlsx 格式的 Excel 文件。
 * 使用 exceljs 库生成文件, 包含两个 sheet:
 *   - "数据" sheet: 查询结果表格 (列名 + 数据行)
 *   - "图表" sheet: ECharts 图表 PNG 快照 (可选)
 *
 * 设计决策:
 *   - 使用 exceljs 而非 xlsx 库: exceljs 支持图片嵌入、样式控制、
 *     流式写入等高级功能, 更适合复杂报表场景
 *   - 数据 sheet 用冻结首行 + 自动列宽: 提升大数据量查看体验
 *   - CSV 注入防护: 以 = + - @ 开头的值加单引号前缀, 防止 Excel 公式执行
 *     (OWASP 推荐防护, 对标 V1 安全审计中发现的问题)
 *   - 图表用 ECharts getDataURL 导出 PNG: 2x pixelRatio 保证高清
 *   - 文件名使用问题前 20 字符 + 时间戳, 避免重复
 *
 * 数据流:
 *   ChatView 中用户点击"导出" → 拼接 ExportData → 调用 exportQueryToExcel
 *   → 生成 workbook → 创建 Blob → 动态创建 <a> 标签触发下载
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
 * 数据 sheet: 表头加粗 + 浅蓝底 + 冻结首行 + 自动列宽, CSV 注入防护。
 * 图表 sheet: 把 ECharts 的 canvas 转 PNG base64 嵌入, 居中显示。
 *
 * @param data - 导出数据, 包含问题、列名、数据行和可选的图表实例
 * @returns void (触发浏览器文件下载)
 *
 * 流程:
 *   1. 创建 ExcelJS Workbook
 *   2. 添加"数据" sheet, 写入表头和数据行
 *   3. 如果有图表, 添加"图表" sheet, 嵌入 PNG
 *   4. 生成 ArrayBuffer, 创建 Blob, 触发下载
 */
export async function exportQueryToExcel(data: ExportData): Promise<void> {
  const { question, columns, rows, chart } = data
  const workbook = new ExcelJS.Workbook()
  workbook.creator = 'ChatBI'
  workbook.created = new Date()

  // ── Sheet 1: 数据 ──────────────────────────────
  // 冻结首行 (ySplit: 1), 方便滚动查看大量数据
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
  // 对每个单元格值调用 sanitizeCell, 防止 = + - @ 开头的公式注入
  for (const row of rows) {
    ws.addRow(columns.map((col) => sanitizeCell(row[col])))
  }

  // 自动列宽 (按内容长度估算, 中文按 2 字符宽)
  // 避免列宽过大或过小, 限制在 10-50 范围内
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
  // 如果传入了 ECharts 实例, 将图表导出为 PNG 嵌入到第二个 sheet
  if (chart) {
    const pngBase64 = chart.getDataURL({
      type: 'png',
      pixelRatio: 2,      // 2x 高清
      backgroundColor: '#fff',  // 白底, 避免透明背景显示异常
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
  // 生成 Excel 文件的 ArrayBuffer, 创建 Blob, 通过 <a> 标签触发下载
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
 * CSV/Excel 注入防护
 *
 * 安全问题: 如果单元格值以 = + - @ 开头, Excel 会将其解释为公式并执行。
 * 攻击者可以通过在查询结果中注入恶意公式来执行命令。
 *
 * 防护措施: 在值前加单引号前缀, Excel 会将其视为纯文本。
 * 这是 OWASP 推荐的 CSV/Excel 注入防护方案。
 *
 * @param value - 原始值
 * @returns 安全的值 (字符串)
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

/**
 * base64 字符串 → ArrayBuffer
 *
 * exceljs 的 addImage 方法需要 ArrayBuffer 格式的图片数据。
 * 将 base64 编码的字符串解码为二进制 ArrayBuffer。
 *
 * @param base64 - base64 编码的字符串 (不含 data:image/png;base64, 前缀)
 * @returns ArrayBuffer 格式的二进制数据
 */
function base64ToBuffer(base64: string): ArrayBuffer {
  const binaryStr = atob(base64)
  const len = binaryStr.length
  const bytes = new Uint8Array(len)
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryStr.charCodeAt(i)
  }
  return bytes.buffer
}

/**
 * 格式化日期时间 (用于文件名)
 *
 * 格式: YYYYMMDD_HHmm (如 "20240101_1430")
 * 不包含秒, 避免文件名过长。
 */
function formatDate(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`
}
