/**
 * 错误处理工具 — 从 Axios 错误中提取可读的错误消息
 *
 * 架构职责:
 *   为前端组件提供统一的错误解析函数, 将后端 FastAPI 返回的
 * 各种错误格式转换为用户可读的字符串。
 *
 * FastAPI 错误格式:
 *   - 业务错误 (HTTP 4xx/5xx):
 *     { "detail": "错误消息" }  → 直接取字符串
 *   - 422 校验错误 (Pydantic 验证失败):
 *     { "detail": [ { "msg": "...", "loc": [...], ... }, ... ] }
 *     → 拼接各 msg 字段
 *   - 网络错误 (请求未到达服务器):
 *     e.message  → 返回 "网络错误" 或类似信息
 *   - 未知错误:
 *     返回 "未知错误"
 *
 * 使用示例:
 *   try {
 *     await api.someRequest()
 *   } catch (e) {
 *     ElMessage.error(extractErrorDetail(e))
 *   }
 *
 * 注意: 此函数不处理 401 错误 (由 api/client.ts 的响应拦截器统一处理),
 * 也不处理 500 错误 (后端应返回标准化错误响应)。
 */
export function extractErrorDetail(e: any): string {
  const detail = e?.response?.data?.detail
  if (!detail) return e?.message || '未知错误'

  // 业务错误: detail 是字符串
  // 如 { "detail": "数据源不存在" }
  if (typeof detail === 'string') return detail

  // 422 校验错误: detail 是数组
  // 如 { "detail": [ { "loc": ["body", "email"], "msg": "field required", "type": "value_error.missing" } ] }
  if (Array.isArray(detail)) {
    return detail
      .map((item: any) => item?.msg || String(item))
      .join('; ')
  }

  // 其他格式, 兜底转字符串
  return String(detail)
}
