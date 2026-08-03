"""
SEC-007: Unicode 清洗 — 用户输入/DB 值进 LLM 前清洗

对标:
  - security-framework spec SEC-007: NFKC + 去零宽/方向控制/私有区字符
  - 防注入: 全角字母绕过 (ＳＥＬＥＣＴ), 零宽字符隐藏指令, RLO 篡改显示

处理:
  1. NFKC 归一化: 全角→半角 (Ｓ→S, １→1), 兼容分解
  2. 去零宽字符: U+200B/200C/200D/FEFF (零宽空格/非连接符/连接符/BOM)
  3. 去方向控制: U+202A-202E (LRE/RLE/PDF/LRO/RLO 方向覆盖)
  4. 去私有区: U+E000-F8FF (私有用途区, 不可显示)

应用点: 用户问题 + DB 字段值进 LLM prompt 前 (intent/retriever/sql_agent)

为什么需要清洗:
  - 全角字母绕过: 如 ＳＥＬＥＣＴ * FROM users (全角字母, LLM 可能不解)
  - 零宽字符隐藏指令: 攻击者插入零宽字符使 LLM 忽视中间内容
  - RLO (U+202E): 反转显示顺序, 篡改 SQL 字面含义
  - 私有区字符: 跨平台显示不一致, 可能被用于混淆

注意: 清洗后文本长度可能变化 (全角→半角), 不影响 token 计算。
"""
from __future__ import annotations

import re
import unicodedata

# 零宽字符: U+200B(零宽空格) U+200C(ZWNJ) U+200D(ZWJ) U+FEFF(BOM)
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
# 方向控制字符: U+202A-202E (LRE/RLE/PDF/LRO/RLO)
_DIRECTIONAL = re.compile(r"[\u202a\u202b\u202c\u202d\u202e]")
# 私有用途区: U+E000-F8FF (基本多文种平面私有区)
_PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")
# 其他格式控制字符 (U+2060-206F word joiner 等, 但保留 \t \n \r 空白)
_FORMAT_CONTROL = re.compile(r"[\u2060-\u2064\u2066-\u206f]")


def sanitize_text(text: str | None) -> str:
    """清洗文本: NFKC + 去危险 Unicode 控制字符。

    对标 SEC-007。空值返回空串。

    清洗流程 (按顺序):
      1. NFKC 归一化: 全角字母/数字→半角, 兼容分解
      2. 去零宽字符: 防止隐藏指令注入
      3. 去方向控制: 防止显示顺序篡改
      4. 去私有区: 防止跨平台显示不一致
      5. 去其他格式控制字符: 词连接符等

    性能: 对短文本 (用户问题) 约 1-5μs, 对长文本 (schema 上下文) 约 50-100μs。
    """
    if not text:
        return ""

    # 1. NFKC 归一化 (全角→半角, 兼容分解)
    # NFKC 比 NFC 更激进: 会把全角字母 (U+FF21→U+0041) 等转为半角
    # 这对 SQL 安全很重要: 全角 SELECT 不会被 parser 识别, 但 LLM 可能产生
    result = unicodedata.normalize("NFKC", text)

    # 2-5. 去各类危险控制字符
    # 分步 sub 而非合并正则, 便于后续扩展 (如新增某个类别)
    result = _ZERO_WIDTH.sub("", result)
    result = _DIRECTIONAL.sub("", result)
    result = _PRIVATE_USE.sub("", result)
    result = _FORMAT_CONTROL.sub("", result)

    return result
