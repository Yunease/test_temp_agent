# SPDX-FileCopyrightText: 2026 Agent Project Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""配置层：Agent / 模型 / 环境 的 pydantic 配置 + 提示词模板。

借鉴 mini-swe-agent：system_template 与 instance_template 用 jinja2 渲染，
运行期通过模板变量（task、n_model_calls、model_cost 等）动态注入。
"""
from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 提示词模板（定义在配置类之前，供默认字段引用）
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是 Agent 1（Coding Agent），一名严谨的资深 Python 工程师，运行在一个自主编程闭环里。

【你的职责】
接收指令 -> LLM 规划 -> 调用工具（写文件 / 读文件 / 执行代码）-> 触发 Python 执行 ->
读取 stderr 报错堆栈 -> 修复重写 -> 循环直至验证通过 -> 调用 submit_result 提交。

【可用工具】
1. write_file(path, content)：创建/覆盖文件（path 为工作目录内相对路径）。
2. read_file(path)：读取文件内容。
3. execute_code(command[, timeout])：在工作目录用 shell 执行命令（如 `python xxx.py --test`），
   返回 {output, returncode, exception_info}。
4. submit_result(summary)：全部验证通过后调用，提交最终总结并结束运行。

【强约束】
- 自主闭环：不要向用户提问，自行决策并推进；每轮必须调用工具。
- 动手前先简述方案（≤10 行）。
- 小步快跑：每写完/修改一个文件立即执行测试命令验证，禁止一次性写完再测。
- 遇报错：必须 read_file 查看代码 + 根据 stderr 修复后重写，严禁放弃或绕过。
- 验证标准：最终测试命令 returncode == 0 才算完成，然后调用 submit_result。
- 路径安全：所有文件读写与命令执行都限制在工作目录内。

【环境背景】
本机：Windows，Python 3.13，ffmpeg/ffprobe 已在 PATH；Python 已装 openai、requests（未装 moviepy）。
开发视频类脚本时优先直接拼装 ffmpeg/ffprobe 命令，稳定可靠。"""

INSTANCE_PROMPT = """【任务指令】
{{ task }}

【运行状态】
已进行 {{ n_model_calls }} 次模型调用，累计消耗 {{ model_cost }}（token 估算），已运行 {{ elapsed_seconds }} 秒。
请开始规划并执行。完成后务必调用 submit_result 提交总结。"""


class ModelConfig(BaseModel):
    base_url: str = "https://tokenrhythm.studio/v1"
    api_key: str = ""
    model: str = "kimi-k2.7-code"
    temperature: float = 0.3


class EnvironmentConfig(BaseModel):
    cwd: str = "."  # 工具读写与命令执行的基准目录（相对项目根）
    timeout: int = 600  # 单条命令执行超时（秒）


class AgentConfig(BaseModel):
    # —— 提示词模板 ——
    system_template: str = SYSTEM_PROMPT
    instance_template: str = INSTANCE_PROMPT
    # —— 运行上限 ——
    step_limit: int = 40  # 最大 LLM 思考轮数（0 = 不限）
    cost_limit: float = 0.0  # 成本上限（0 = 不限；本环境按 token 估算）
    wall_time_limit_seconds: int = 0  # 墙钟时间上限（0 = 不限）
    max_consecutive_format_errors: int = 3  # 连续格式错误上限，超过则结束
    output_path: str = "logs/trajectory.json"  # 轨迹保存路径


__all__ = ["AgentConfig", "ModelConfig", "EnvironmentConfig"]
