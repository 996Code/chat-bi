/**
 * 从 Axios 错误中提取可读的错误消息。
 *
 * FastAPI 返回两种格式:
 * - 业务错误: { detail: "错误消息" }  → 直接取字符串
 * - 422 校验错误: { detail: [{ msg: "...", ... }, ...] }  → 拼接各 msg
 */
export function extractErrorDetail(e: any): string {
  const detail = e?.response?.data?.detail
  if (!detail) return e?.message || '未知错误'

  // 业务错误: detail 是字符串
  if (typeof detail === 'string') return detail

  // 422 校验错误: detail 是数组
  if (Array.isArray(detail)) {
    return detail
      .map((item: any) => item?.msg || String(item))
      .join('; ')
  }

  return String(detail)
}
