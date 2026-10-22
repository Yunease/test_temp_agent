#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 1 —— Coding Agent（母体）
================================
一个具备"自主编程闭环"能力的最小核心 Agent：

    接收指令 -> LLM 规划 -> Tool Call（write_file / read_file / execute_code）
    -> 触发 Python 执行 -> 读取 stderr 报错堆栈 -> 修复重写 -> 直至成功

三个基础工具（Tool/Action）：
    1. write_file(path, content)  创建/覆盖写文件
    2. execute_code(command)      subprocess 执行命令，严格捕获 stdout/stderr/returncode
    3. read_file(path)            读取现有代码用于修改

用法：
    python coding_agent.py --task "任务指令" [--log execution.log] [--max-rounds 40]
    python coding_agent.py --task-file task.txt
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# 日志：同时输出到终端与日志文件（交付材料：完整执行日志）
# ---------------------------------------------------------------------------
class Logger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(log_path, "a", encoding="utf-8")
        self.token_prompt = 0
        self.token_completion = 0
        self.llm_calls = 0
        self.tool_calls = 0

    def log(self, msg: str = ""):
        line = msg
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def section(self, title: str):
        bar = "=" * 72
        self.log(f"\n{bar}\n[{datetime.now():%H:%M:%S}] {title}\n{bar}")

    def usage_add(self, usage):
        if usage:
            self.llm_calls += 1
            self.token_prompt += getattr(usage, "prompt_tokens", 0) or 0
            self.token_completion += getattr(usage, "completion_tokens", 0) or 0

    def summary(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "prompt_tokens": self.token_prompt,
            "completion_tokens": self.token_completion,
            "total_tokens": self.token_prompt + self.token_completion,
        }


# ---------------------------------------------------------------------------
# Meta-Prompt：强约束系统提示词（指导如何规划视频剪辑 Agent）
# ---------------------------------------------------------------------------
META_PROMPT = """你是 Agent 1（Coding Agent），一名严谨的资深 Python 工程师。你的职责是自主完成编程任务：规划、写代码、执行、读报错、修复，直到全部验证通过。

【工作目录】所有文件读写与命令执行都在当前工作目录内，禁止访问工作目录之外的路径。

【可用工具】
1. write_file(path, content)：创建/覆盖文件（path 为工作目录内的相对路径）。
2. read_file(path)：读取文件内容。
3. execute_code(command)：用 subprocess 在工作目录执行 shell 命令（如 python xxx.py），返回 stdout、stderr、returncode。

【强约束】
- 自主闭环：不要向用户提问，自行决策并完成任务；每轮必须调用工具推进。
- 先规划后动手：动手前先简述方案（不超过 10 行）。
- 小步快跑：每写完一个文件立即执行测试命令验证，禁止一次性写完再测。
- 遇到报错：必须 read_file 查看代码、根据 stderr 堆栈修复后重写，严禁放弃或绕过。
- 验证标准：最终测试命令 returncode == 0 才算完成。
- 完成标志：所有验证通过后，输出最终总结（文件清单、测试结果、returncode）。

【当前任务背景】你将负责开发一个"视频剪辑 Agent"（video_agent.py）。本机环境：
- Windows，Python 3.13
- 已安装 ffmpeg / ffprobe（在 PATH 中）
- Python 已装库：openai、requests（未装 moviepy）

开发该类脚本时优先直接调用 ffmpeg/ffprobe 命令行（稳定可靠），也可 pip 安装轻量库。"""

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写一个文件（相对路径，限制在工作目录内）",
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
            "description": "在工作目录用 shell 执行命令（如 python xxx.py --test），返回 stdout/stderr/returncode",
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
]


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
def _safe_path(path: str) -> Path:
    """把相对路径限制在工作目录内，防止越权读写。"""
    p = (ROOT / path).resolve()
    if ROOT not in p.parents and p != ROOT:
        raise ValueError(f"路径越界（必须在工作目录内）: {path}")
    return p


def tool_write_file(path: str, content: str) -> str:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"OK: 已写入 {path}（{len(content)} 字符）"


def tool_read_file(path: str) -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"ERROR: 文件不存在: {path}"
    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > 60000:
        text = text[:60000] + "\n...[内容过长已截断]"
    return text


def tool_execute_code(command: str, timeout: int = 600) -> str:
    try:
        r = subprocess.run(
            command, shell=True, cwd=str(ROOT), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return json.dumps(
            {"returncode": r.returncode, "stdout": r.stdout[-20000:], "stderr": r.stderr[-20000:]},
            ensure_ascii=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"returncode": -1, "stdout": "", "stderr": f"命令超时（>{timeout}s）"}, ensure_ascii=False)


def dispatch_tool(name: str, args: dict) -> str:
    if name == "write_file":
        return tool_write_file(args["path"], args["content"])
    if name == "read_file":
        return tool_read_file(args["path"])
    if name == "execute_code":
        return tool_execute_code(args["command"], int(args.get("timeout", 600)))
    return f"ERROR: 未知工具 {name}"


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------
class CodingAgent:
    def __init__(self, cfg: dict, logger: Logger, max_rounds: int = 40):
        self.client = OpenAI(
            base_url=cfg.get("base_url") or os.environ.get("CODING_AGENT_BASE_URL"),
            api_key=cfg.get("api_key") or os.environ.get("CODING_AGENT_API_KEY"),
            timeout=300,
        )
        self.model = cfg.get("model") or os.environ.get("CODING_AGENT_MODEL", "gpt-4o-mini")
        self.logger = logger
        self.max_rounds = max_rounds

    def run(self, task: str) -> bool:
        log = self.logger
        messages = [
            {"role": "system", "content": META_PROMPT},
            {"role": "user", "content": f"【任务指令】\n{task}"},
        ]

        log.section("接收指令")
        log(task)

        for round_no in range(1, self.max_rounds + 1):
            log.section(f"LLM 第 {round_no} 轮思考")
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=TOOLS_SCHEMA, temperature=0.3,
            )
            log.usage_add(resp.usage)
            msg = resp.choices[0].message
            finish = resp.choices[0].finish_reason
            if msg.content:
                log(f"[模型输出]\n{msg.content}")

            if not msg.tool_calls:
                log.section("任务结束（模型不再调用工具）")
                log(f"finish_reason={finish}")
                log.section("执行统计")
                log(json.dumps(log.summary(), ensure_ascii=False, indent=2))
                return finish == "stop"

            messages.append(msg.model_dump(exclude_none=True))

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                log.section(f"Tool Call -> {name}")
                arg_show = {k: (v[:200] + f"...[共{len(v)}字符]" if isinstance(v, str) and len(v) > 200 else v)
                            for k, v in args.items()}
                log(f"参数: {json.dumps(arg_show, ensure_ascii=False)}")
                result = dispatch_tool(name, args)
                self.logger.tool_calls += 1
                log.section(f"工具结果 <- {name}")
                log(result if len(result) <= 8000 else result[:8000] + f"\n...[共{len(result)}字符]")

                # 触发 Python 执行时，把 returncode 显式标注出来，便于追踪自修复闭环
                if name == "execute_code":
                    try:
                        rc = json.loads(result).get("returncode")
                        if rc == 0:
                            log(f">>> 执行成功 returncode=0")
                        else:
                            log(f">>> 执行失败 returncode={rc}（请根据 stderr 修复后重试）")
                    except json.JSONDecodeError:
                        pass

                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        log.section("达到最大轮数限制，任务未完成")
        return False


def load_config() -> dict:
    """配置优先级：环境变量 > config.json > 默认值"""
    cfg = {}
    cfg_file = ROOT / "config.json"
    if cfg_file.exists():
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    for env_key, cfg_key in [
        ("CODING_AGENT_API_KEY", "api_key"),
        ("CODING_AGENT_BASE_URL", "base_url"),
        ("CODING_AGENT_MODEL", "model"),
    ]:
        if os.environ.get(env_key):
            cfg[cfg_key] = os.environ[env_key]
    if not cfg.get("api_key"):
        sys.exit("错误：未配置 API Key（请设置 CODING_AGENT_API_KEY 或提供 config.json）")
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Agent 1: Coding Agent（自主编程闭环母体）")
    parser.add_argument("--task", help="任务指令")
    parser.add_argument("--task-file", help="从文件读取任务指令")
    parser.add_argument("--log", default="logs/execution.log", help="日志输出路径")
    parser.add_argument("--max-rounds", type=int, default=40)
    args = parser.parse_args()

    task = args.task or (Path(args.task_file).read_text(encoding="utf-8") if args.task_file else None)
    if not task:
        parser.error("必须通过 --task 或 --task-file 提供任务指令")

    cfg = load_config()
    logger = Logger(ROOT / args.log)
    logger.section(f"Coding Agent 启动 | model={cfg.get('model')} | {datetime.now():%Y-%m-%d %H:%M:%S}")

    ok = CodingAgent(cfg, logger, args.max_rounds).run(task)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
