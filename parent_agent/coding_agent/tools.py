# SPDX-FileCopyrightText: 2026 Agent Project Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""工具层（Tool / Action 实现）。

定义 3 个基础工具 + 1 个终止工具：
    write_file / read_file / execute_code / submit_result

每个动作以 dict 形式传递：{"name", "arguments", "tool_call_id"}。
run_action 统一调度，返回结构化的执行结果 dict。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

# 工具 Schema（OpenAI 函数调用格式）—— 注入给模型
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写一个文件（path 为工作目录内相对路径）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件相对路径"},
                    "content": {"type": "string", "description": "文件完整内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作目录内某个文件的内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件相对路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": ("在工作目录用 shell 执行命令（如 `python xxx.py --test`），"
                            "返回 {output, returncode, exception_info}"),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 600"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_result",
            "description": "全部验证通过后调用，提交最终总结并结束 Agent 运行",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "最终总结：文件清单、测试结果、returncode"},
                },
                "required": ["summary"],
            },
        },
    },
]

# 终止工具名称
SUBMIT_TOOL = "submit_result"


def _safe_path(path: str, root: Path) -> Path:
    """把相对路径限制在工作目录内，防止越权读写。"""
    p = (root / path).resolve()
    if root.resolve() not in p.parents and p != root.resolve():
        raise ValueError(f"路径越界（必须在工作目录内）: {path}")
    return p


def run_action(action: dict, root: Path) -> dict:
    """执行单个动作，返回结构化结果。

    成功返回 {"output", "returncode", "exception_info": ""}；
    异常返回 {"output", "returncode": -1, "exception_info": ..., "extra": {...}}。
    """
    name = action.get("name", "")
    args = action.get("arguments", {}) or {}

    if name == "write_file":
        return _write_file(args, root)
    if name == "read_file":
        return _read_file(args, root)
    if name == "execute_code":
        return _execute_code(args, root)
    if name == SUBMIT_TOOL:
        return {"output": "", "returncode": 0, "exception_info": "", "extra": {"submitted": True}}
    return {
        "output": "",
        "returncode": -1,
        "exception_info": f"未知工具 '{name}'",
        "extra": {"exception_type": "UnknownTool"},
    }


# ---------------------------------------------------------------------------
# 具体工具实现
# ---------------------------------------------------------------------------
def _write_file(args: dict, root: Path) -> dict:
    try:
        p = _safe_path(args["path"], root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""), encoding="utf-8")
        return {"output": f"OK: 已写入 {args['path']}（{len(args.get('content', ''))} 字符）",
                "returncode": 0, "exception_info": ""}
    except Exception as e:  # noqa: BLE001
        return {"output": "", "returncode": -1,
                "exception_info": f"write_file 失败: {e}", "extra": {"exception_type": type(e).__name__}}


def _read_file(args: dict, root: Path) -> dict:
    try:
        p = _safe_path(args["path"], root)
        if not p.exists():
            return {"output": f"ERROR: 文件不存在: {args['path']}", "returncode": 1, "exception_info": ""}
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > 60000:
            text = text[:60000] + "\n...[内容过长已截断]"
        return {"output": text, "returncode": 0, "exception_info": ""}
    except Exception as e:  # noqa: BLE001
        return {"output": "", "returncode": -1,
                "exception_info": f"read_file 失败: {e}", "extra": {"exception_type": type(e).__name__}}


def _execute_code(args: dict, root: Path) -> dict:
    command = args.get("command", "")
    timeout = int(args.get("timeout", 600))
    output = {"output": "", "returncode": -1, "exception_info": ""}
    try:
        r = subprocess.run(
            command, shell=True, cwd=str(root), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        output["output"] = (r.stdout or "") + (r.stderr or "")
        output["returncode"] = r.returncode
    except subprocess.TimeoutExpired as e:
        output["output"] = (getattr(e, "output", None) or "")
        output["exception_info"] = f"命令超时（>{timeout}s）"
        output["extra"] = {"exception_type": "TimeoutExpired"}
    except Exception as e:  # noqa: BLE001
        output["exception_info"] = f"命令执行异常: {e}"
        output["extra"] = {"exception_type": type(e).__name__}
    return output
