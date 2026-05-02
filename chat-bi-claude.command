#!/bin/bash
# FamilyMemo — Mac 双击启动 Claude Code
# 双击此文件即可在终端中打开 Claude Code 会话

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 如果 claude 命令不存在，提示安装
if ! command -v claude &> /dev/null; then
    echo "错误: 未找到 claude 命令"
    echo "请先安装 Claude Code: npm install -g @anthropic-ai/claude-code"
    echo ""
    read -p "按回车键退出..."
    exit 1
fi

claude -r --permission-mode bypassPermissions
