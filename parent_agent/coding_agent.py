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

# 允许从项目根目录直接运行（无需安装）
# ROOT 为本文件所在目录（parent_agent/），PROJECT_ROOT 为项目根（母体在此目录下读写子体与素材）
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from coding_agent import CodingAgent, LocalEnvironment, Model, render_human_log
from coding_agent.config import AgentConfig, EnvironmentConfig, ModelConfig


def load_model_config() -> dict:
    """配置优先级：命令行/环境变量 > config.json > 默认值。"""
    cfg_file = ROOT / "config.json"
    cfg = {}
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
    parser.add_argument("--config", default=str(ROOT / "config.json"), help="配置文件路径")
    parser.add_argument("--log", default="logs/execution.log", help="人类可读日志输出路径")
    parser.add_argument("--trajectory", default="logs/trajectory.json", help="结构化轨迹输出路径")
    parser.add_argument("--max-rounds", type=int, default=40, help="最大 LLM 思考轮数")
    parser.add_argument("--timeout", type=int, default=600, help="单条命令执行超时（秒）")
    args = parser.parse_args()

    task = args.task or (Path(args.task_file).read_text(encoding="utf-8") if args.task_file else None)
    if not task:
        parser.error("必须通过 --task 或 --task-file 提供任务指令")

    model_cfg = load_model_config()

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
