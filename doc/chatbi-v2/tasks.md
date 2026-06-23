# Tasks

> ⚠️ **任务清单的单一真相源是 `openspec/changes/chatbi-v2/tasks.md`**。
>
> 本文件已废弃，仅保留指向。`/ai:resume`、`/ai:do`、`/ai:check`、`openspec list` 都读 OpenSpec 版。

## 去哪里看任务

**当前任务与进度**：[`openspec/changes/chatbi-v2/tasks.md`](../../../openspec/changes/chatbi-v2/tasks.md)

查看进度：
```bash
openspec list                    # 11/54 tasks
cat openspec/changes/chatbi-v2/tasks.md
```

## 为什么有两份

历史上 `doc/chatbi-v2/tasks.md` 和 `openspec/changes/chatbi-v2/tasks.md` 内容重复，维护两份必然不一致（对标 v1 教训 #11：双真相源）。现统一以 OpenSpec 版为准——因为 `/ai:*` 工作流和 `openspec` CLI 实际读取的是它。

如需任务细节，请直接读 `openspec/changes/chatbi-v2/tasks.md`。
