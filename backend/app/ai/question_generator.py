"""
示例问题生成器 — 扫描数据源时生成"用户可能会问的 BI 问题"

对标:
  - V1 设计: 扫描完告诉用户这个数据源可以问什么 (空状态引导)
  - Claude Code: 降低用户认知负担, 给出可探索的入口

设计:
  - generate_sample_questions: 基于扫描到的表/列/关系, LLM 生成 6-8 个 BI 问题
  - Fail-closed (根本模式): LLM 失败 → 基于列语义类型的规则降级 (measure/dimension 组合)
  - 对标 replier/thinking/chart_agent 的降级模式: LLM 失败总有兜底, 不返回空

数据流总览:
  1. 输入: 语义层扫描结果 (models: list[Model])
  2. 主路径: LLM 生成 6-8 个业务问题
  3. 降级路径: LLM 失败 → _fallback_questions 规则生成
  4. 极端兜底: 规则也无法生成 → 返回硬编码通用问题
  5. 输出: 6-8 个示例问题列表 (前端展示给新用户)

Fail-Closed 保证:
  - LLM 失败 → 规则降级
  - 规则生成空 → 硬编码兜底
  - 每个路径都保证返回非空列表
"""
from __future__ import annotations

import json
import logging
import re

from app.schemas.semantic_layer import Model

logger = logging.getLogger(__name__)


_QUESTION_PROMPT = """你是 BI 数据分析师。根据下面的数据库表结构, 生成 6-8 个用户最可能问的业务分析问题。

表结构:
{schema}

要求:
1. 问题要具体、可直接用 SQL 回答 (聚合/排名/占比/趋势)
2. 覆盖不同表和不同分析角度 (不要只问一张表)
3. 用自然语言, 不要出现 SQL/表名/列名, 用业务术语
4. 每行一个问题, 不要编号, 不要解释

示例格式:
本月各品类的销售额排名
消费金额最高的前10个用户
不同状态的订单数量占比
"""


async def generate_sample_questions(
    models: list[Model],
) -> list[str]:
    """基于语义层生成示例 BI 问题 (LLM → 规则降级)。

    Args:
        models: 扫描到的语义层表列表 (含列/关系)

    Returns:
        6-8 个示例问题 (fail-closed: LLM 失败用规则降级, 不返回空)

    数据流:
      1. 构建 schema_summary (过滤系统表, 提取 measure/dimension/关系)
      2. 调用 LLM 生成问题 (temperature=0.3 保证一定多样性)
      3. 解析 LLM 输出 (去编号/去空行)
      4. LLM 失败/空结果 → 规则降级 _fallback_questions
      5. 规则降级也失败 → 硬编码兜底 3 个通用问题
    """
    schema_text = _build_schema_summary(models)
    if not schema_text:
        # schema 为空 (如无业务表) → 直接规则降级, 跳过 LLM 调用
        return _fallback_questions(models)

    prompt = _QUESTION_PROMPT.format(schema=schema_text)

    try:
        from app.core.llm_client import llm_chat
        system_msg = "你是 BI 分析专家, 生成用户最可能问的业务问题。"
        content, _ = await llm_chat(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            node="question_generator",
            temperature=0.3,
        )
        questions = _parse_questions(content)
        if questions:
            logger.info("LLM 生成 %d 个示例问题", len(questions))
            return questions
        logger.warning("LLM 示例问题为空, 降级规则生成")
        return _fallback_questions(models)
    except Exception as e:
        # 异常兜底: 不抛到上层, 降级规则生成
        logger.warning("示例问题 LLM 生成失败, 降级规则生成: %s", e)
        return _fallback_questions(models)


def _build_schema_summary(models: list[Model]) -> str:
    """构建喂给 LLM 的表结构摘要 (表名+中文名+关键列)。

    数据流:
      1. 输入: 语义层 models 列表
      2. 过滤: 排除系统表 (metadata 表, 用户不需要关心)
      3. 提取: 每个表取 measure 前 3 个 + dimension 前 4 个 + 关系
      4. 输出: 格式化文本 → 注入 LLM prompt

    设计决策:
      - 只取 measure 和 dimension 列, 跳过 system/unknown 类型列
      - measure 取前 3, dimension 取前 4: 控制 prompt 长度, 避免超标
      - 系统表过滤: 集中定义在 config.system_tables, 避免硬编码
    """
    if not models:
        return ""
    # 过滤掉系统表 (ChatBI 元数据表, 集中定义在 config)
    from app.core.config import get_settings
    system_tables = frozenset(get_settings().system_tables)
    lines = []
    for m in models:
        if m.name in system_tables:
            continue
        # 取关键列: measure (度量) + dimension (维度) 各几个
        # 限制数量: 控制 LLM prompt 长度, 避免表列过多导致 token 超限
        measures = [c.display_name for c in m.columns if c.semantic_type == "measure"][:3]
        dimensions = [c.display_name for c in m.columns if c.semantic_type == "dimension"][:4]
        rels = [r.target_model for r in m.relationships]
        parts = [f"- {m.display_name}({m.name})"]
        if measures:
            parts.append(f"度量: {', '.join(measures)}")
        if dimensions:
            parts.append(f"维度: {', '.join(dimensions)}")
        if rels:
            parts.append(f"关联: {', '.join(rels)}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _parse_questions(text: str) -> list[str]:
    """从 LLM 输出解析问题列表 (每行一个, 去编号/空行)。

    数据流:
      1. 输入: LLM 原始输出文本 (可能含编号、空行、前后缀说明)
      2. 处理: 按行分割 → 去掉编号 (~ 1. 2. -) → 去空行 → 长度过滤
      3. 输出: 最多 8 个问题

    边界情况:
      - 空文本 → 返回空列表 (触发调用方降级)
      - 含编号行 (1. 2. -) → 去掉编号保留内容
      - 短于 4 字符的行 → 过滤 (可能是残留的标点或缩写)
      - 超过 8 行 → 截断 (prompt 要求 6-8, 但 LLM 可能生成更多)
    """
    questions = []
    for line in text.strip().split("\n"):
        line = line.strip()
        # 去掉行首编号 (1. 2. - 等)
        line = re.sub(r"^[\d]+[.、)]\s*", "", line)
        line = re.sub(r"^[-*]\s*", "", line)
        if line and len(line) >= 4:
            questions.append(line)
    return questions[:8]


def _fallback_questions(models: list[Model]) -> list[str]:
    """规则降级: 基于列语义类型生成基础问题 (LLM 失败时的安全降级)。

    策略:
      - 找到有 measure 列的表 → "各{dimension}的{measure}总和/排名"
      - 找到有 dimension 列的表 → "各{dimension}的数量统计"
      - 保证至少返回几个可用问题

    设计决策:
      - 优先组合 measure + dimension: 生成聚合 + 分组问题, 是 BI 最典型场景
      - 只有 measure: 生成汇总统计问题 (如"总销售额")
      - 只有 dimension: 生成数量统计问题 (如"各城市数量")

    极端兜底:
      - 如果规则也无法生成 (如无业务表), 返回 3 个硬编码通用问题
      - 保证前端空状态引导永远有内容可展示

    数据流:
      LLM 失败 → _fallback_questions → 遍历 models → 规则生成 → 截断前 8 个
    """
    from app.core.config import get_settings
    system_tables = frozenset(get_settings().system_tables)
    questions = []

    for m in models:
        if m.name in system_tables:
            continue
        measures = [c for c in m.columns if c.semantic_type == "measure"]
        dimensions = [c for c in m.columns if c.semantic_type == "dimension"]
        table_label = m.display_name or m.name

        if measures and dimensions:
            # "{表}各{维度}的{度量}排名"
            dim = dimensions[0].display_name
            mea = measures[0].display_name
            questions.append(f"{table_label}中各{dim}的{mea}排名")
            questions.append(f"{table_label}中{mea}最高的前10条记录")
        elif dimensions:
            dim = dimensions[0].display_name
            questions.append(f"{table_label}中各{dim}的数量统计")
        elif measures:
            mea = measures[0].display_name
            questions.append(f"{table_label}中{mea}的汇总统计")

        if len(questions) >= 6:
            break

    # 极端兜底: 一个规则问题都没生成 (无业务表)
    if not questions:
        questions = [
            "本月数据概览",
            "最近新增的记录",
            "各分类的数量统计",
        ]

    return questions[:8]
