"""轨迹渲染：把结构化轨迹（trajectory.json）渲染为人类可读的 execution.log。

交付要求：日志需清晰展现
    接收指令 -> Tool Call -> 触发 Python 执行 -> 报错堆栈 -> 修复重试 -> 成功生成
这里从保存的轨迹消息逐个还原这些环节。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _truncate(text: str, n: int = 1500) -> str:
    if text is None:
        return ""
    text = text.strip()
    if len(text) > n:
        return text[:n] + f"\n...[输出过长已截断，共 {len(text)} 字符]"
    return text


def render_human_log(data: dict, path: Path) -> str:
    """根据轨迹数据生成可读日志文本并写入 path。"""
    messages = data.get("messages", [])
    info = data.get("info", {})
    lines: list[str] = []
    bar = "=" * 72

    lines.append(f"{bar}\n Coding Agent 执行轨迹（结构化可复核）\n{bar}\n")
    lines.append(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"模型：{info.get('model_stats', {}).get('api_calls')} 次调用 | "
                 f"Token：{info.get('model_stats', {}).get('total_tokens')}")
    lines.append(f"退出状态：{info.get('exit_status', '')}\n")

    for i, m in enumerate(messages, 1):
        role = m.get("role")
        extra = m.get("extra", {}) or {}
        if role == "system":
            continue
        if role == "user" and i <= 2 and "task" not in " ".join(lines):  # 首条 user = 任务指令
            lines.append(f"\n{bar}\n【接收指令】\n{bar}")
            lines.append(_truncate(m.get("content", ""), 4000))
            continue
        if role == "assistant":
            lines.append(f"\n{bar}\n【第 {i} 轮 · 模型决策 / Tool Call】\n{bar}")
            if m.get("content"):
                lines.append(f"[思考] {_truncate(m['content'], 2000)}")
            for tc in m.get("tool_calls", []):
                args = tc.get("arguments", "")
                args_show = _truncate(args, 600)
                lines.append(f"  ▶ 调用工具：{tc.get('name')}")
                lines.append(f"    参数：{args_show}")
            continue
        if role == "tool":
            rc = extra.get("returncode")
            status = "✅ 成功" if rc == 0 else f"❌ 失败(returncode={rc})"
            lines.append(f"\n  —— 工具结果：{extra.get('name', 'tool')} {status} ——")
            lines.append(_truncate(m.get("content", ""), 2500))
            continue
        if role == "exit":
            lines.append(f"\n{bar}\n【Agent 结束 · {extra.get('exit_status', '')}】\n{bar}")
            lines.append(_truncate(m.get("content", ""), 4000))
            continue

    lines.append(f"\n{bar}\n【执行统计】\n{bar}")
    lines.append(json.dumps(info.get("model_stats", {}), ensure_ascii=False, indent=2))
    lines.append(f"exit_status：{info.get('exit_status', '')}")

    text = "\n".join(lines) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text
