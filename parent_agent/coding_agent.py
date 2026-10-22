#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Agent Project Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Agent 1 —— Coding Agent（母体，模块化 CLI 入口）

用法：
    python parent_agent/coding_agent.py --task "任务指令" [--config parent_agent/config.json]
    python parent_agent/coding_agent.py --task-file task.txt --log execution.log

模块结构见 parent_agent/coding_agent/ 包：config / model / tools / environment / agent / trajectory。
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# 允许从项目根目录直接运行（无需安装）
# ROOT 为本文件所在目录（parent_agent/），PROJECT_ROOT 为项目根（母体在此目录下读写子体与素材）
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from coding_agent import CodingAgent, LocalEnvironment, Model, render_human_log
from coding_agent.config import AgentConfig, EnvironmentConfig, ModelConfig


def load_model_config(config_path: Optional[str] = None) -> dict:
    """配置优先级：环境变量 > config.json > 默认值。"""
    cfg_file = Path(config_path).expanduser() if config_path else (ROOT / "config.json")
    cfg = {}
    if cfg_file.exists():
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    elif config_path:
        sys.exit(f"错误：配置文件不存在: {cfg_file}")
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


def load_task(args, parser) -> str:
    """解析任务指令：--task 优先，其次 --task-file。

    文件缺失/为空时给出可操作的提示并退出（code=2），而不是抛出原始堆栈。
    """
    if args.task and args.task.strip():
        return args.task

    if not args.task_file:
        parser.error("必须通过 --task 或 --task-file 提供任务指令")

    task_path = Path(args.task_file).expanduser()
    if not task_path.is_absolute():
        task_path = Path.cwd() / task_path
        if not task_path.is_file():
            alt = PROJECT_ROOT / args.task_file
            if alt.is_file():
                task_path = alt

    if not task_path.is_file():
        task_files = sorted(p.name for p in PROJECT_ROOT.glob("task*.txt"))
        available = task_files or sorted(p.name for p in PROJECT_ROOT.glob("*.txt"))
        hint = "、".join(available) if available else "（未找到任何 .txt 文件）"
        parser.error(
            f"任务文件不存在: {args.task_file}\n"
            f"  已查找: {Path.cwd() / args.task_file}\n"
            f"  项目根目录可用的任务文件: {hint}\n"
            f"  示例: python parent_agent/coding_agent.py --task-file task_phase2.txt --max-rounds 40"
        )

    try:
        content = task_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = task_path.read_text(encoding="gbk")

    if not content.strip():
        parser.error(f"任务文件内容为空: {task_path}")
    return content


def main():
    parser = argparse.ArgumentParser(description="Agent 1: Coding Agent（自主编程闭环母体）")
    parser.add_argument("--task", help="任务指令")
    parser.add_argument("--task-file", help="从文件读取任务指令")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 parent_agent/config.json）")
    parser.add_argument("--log", default="logs/execution.log", help="人类可读日志输出路径")
    parser.add_argument("--trajectory", default="logs/trajectory.json", help="结构化轨迹输出路径")
    parser.add_argument("--max-rounds", type=int, default=40, help="最大 LLM 思考轮数")
    parser.add_argument("--timeout", type=int, default=600, help="单条命令执行超时（秒）")
    args = parser.parse_args()

    task = load_task(args, parser)

    model_cfg = load_model_config(args.config)

    # 确保日志/轨迹输出目录存在（干净 clone 中可能没有 logs/）
    Path(args.trajectory).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(args.log).expanduser().parent.mkdir(parents=True, exist_ok=True)

    model = Model(config_class=ModelConfig, **model_cfg)
    env = LocalEnvironment(config_class=EnvironmentConfig, root=PROJECT_ROOT, cwd=str(PROJECT_ROOT), timeout=args.timeout)
    agent = CodingAgent(
        model, env,
        config_class=AgentConfig,
        step_limit=args.max_rounds,
        output_path=args.trajectory,
    )

    print(f"[启动] Coding Agent | model={model_cfg.get('model')} | 任务长度={len(task)}")
    result = agent.run(task=task)

    # 渲染人类可读日志
    traj_path = Path(args.trajectory)
    if traj_path.exists():
        data = json.loads(traj_path.read_text(encoding="utf-8"))
        log_path = Path(args.log)
        render_human_log(data, log_path)
        print(f"[完成] exit_status={result.get('exit_status')} | 日志已写入 {log_path}")

    sys.exit(0 if result.get("exit_status") == "Submitted" else 1)


if __name__ == "__main__":
    main()
