"""
语义层 diff 工具 (DSO-04 元数据自动刷新依赖)

从 semantic_models.py 的 diff 端点抽取, 补列级 diff。
供 diff 端点 + 元数据自动刷新 (detect_and_refresh_metadata) 复用。

设计:
  - diff_semantic_contents(old, new) 对比两个语义层 content dict
  - 表级: 新增/删除/变更表
  - 列级: 变更表里的新增/删除/变更列 (类型/语义类型变化)
  - 纯函数, 不依赖 DB (易测试)

设计决策:
  - 为什么是纯函数: 元数据刷新和 diff 端点都需要对比, 且业务逻辑相同。
    纯函数不依赖 DB/网络, 方便单元测试, 也避免与外部服务耦合。
  - 为什么列级只对比 data_type 和 semantic_type:
    其他字段 (display_name/description) 是人工/LLM 标注, 结构扫描不产生,
    对比这些字段没有意义 (它们变化不代表结构变化)。
  - 为什么返回 sorted list: 确保 diff 结果稳定, 方便前端展示和自动化测试。
    排序 key 是表名/列名, 不依赖 dict 插入顺序。
"""

# 对比的列属性: 只关注语义相关的属性变化
# data_type: 数据库类型变化 (如 VARCHAR→TEXT) 影响 SQL 生成
# semantic_type: 语义类型变化 (如 普通列→度量) 影响 BI 分析
from __future__ import annotations

_COL_DIFF_ATTRS = ("data_type", "semantic_type")


def diff_semantic_contents(old: dict, new: dict) -> dict:
    """对比两个语义层 content 的差异。

    Args:
        old: 旧版本 content dict (含 "models" 列表)
        new: 新版本 content dict

    Returns:
        {
            added_models: [新增表名],
            removed_models: [删除表名],
            changed_models: [{table, added_columns, removed_columns, changed_columns}],
            unchanged_models: [无变化表名],
            has_changes: bool  # 是否有任何差异 (元数据刷新用)
        }

    算法:
    1. 以表名为 key 建立 index
    2. 集合运算: 新增 = new - old, 删除 = old - new, 共同 = old & new
    3. 对共同表: 对比列级变更 + 顶层字段变更
    4. 汇总 has_changes (元数据刷新用这个字段决定是否写新版本)
    """
    # 使用空 dict fallback: 如果 old/new 为 None, 视为空内容
    # 这样传入 None 不会崩溃, 适用于初始状态对比
    old_models = {m["name"]: m for m in (old or {}).get("models", [])}
    new_models = {m["name"]: m for m in (new or {}).get("models", [])}

    # 集合运算: 新增/删除/共同表
    # 表名是唯一标识, 不关心表名大小写 (数据库通常不区分, 但这里精确匹配)
    added = sorted(set(new_models) - set(old_models))
    removed = sorted(set(old_models) - set(new_models))
    common = sorted(set(old_models) & set(new_models))

    changed_models = []
    unchanged = []
    for name in common:
        # 列级 diff
        col_diff = _diff_columns(old_models[name], new_models[name])
        # 整表对比 (display_name/description 等顶层字段也算变更)
        # 这些字段即使结构不变, 人工/LLM 修改了也算变更
        top_changed = any(
            old_models[name].get(k) != new_models[name].get(k)
            for k in ("display_name", "description", "source", "confidence")
        )
        if col_diff["has_column_changes"] or top_changed:
            changed_models.append({"table": name, **col_diff})
        else:
            unchanged.append(name)

    # has_changes 是元数据刷新判断是否写新版本的依据
    has_changes = bool(added or removed or changed_models)
    return {
        "added_models": added,
        "removed_models": removed,
        "changed_models": changed_models,
        "unchanged_models": unchanged,
        "has_changes": has_changes,
    }


def _diff_columns(old_model: dict, new_model: dict) -> dict:
    """对比单个表的列差异。

    对比列名集合 (增删) + 同名列的关键属性 (data_type/semantic_type 变更)。

    为什么只对比 data_type 和 semantic_type:
    - data_type: 数据库类型变化 (如 VARCHAR(100)→TEXT) 意味着展示/处理方式不同
    - semantic_type: 语义类型变化 (如 dimension→measure) 影响 BI 分析逻辑
    - display_name/description 变化不反映结构变更, 不影响元数据版本管理

    边界情况:
    - 列名大小写: 精确匹配, 不忽略大小写 (数据库可能区分大小写)
    - 列顺序变化: 不影响 diff (以列名为 key, 不关心顺序)
    - 同名列: 只有指定属性变化才标记为 changed
    """
    old_cols = {c["name"]: c for c in old_model.get("columns", [])}
    new_cols = {c["name"]: c for c in new_model.get("columns", [])}

    added_columns = sorted(set(new_cols) - set(old_cols))
    removed_columns = sorted(set(old_cols) - set(new_cols))

    # 同名列的关键属性变更 (类型/语义类型)
    # 使用 _COL_DIFF_ATTRS 常量, 便于统一修改对比维度
    common_cols = set(old_cols) & set(new_cols)
    changed_columns = []
    for col_name in common_cols:
        for attr in _COL_DIFF_ATTRS:
            if old_cols[col_name].get(attr) != new_cols[col_name].get(attr):
                changed_columns.append(col_name)
                break  # 一个属性变化就标记, 不重复追加

    return {
        "added_columns": added_columns,
        "removed_columns": removed_columns,
        "changed_columns": sorted(set(changed_columns)),  # dedup + 排序
        "has_column_changes": bool(added_columns or removed_columns or changed_columns),
    }


def is_empty_diff(diff_result: dict) -> bool:
    """便捷判断: diff 结果是否无任何变化 (元数据刷新用)。"""
    return not diff_result.get("has_changes", False)
