"""coding_agent: 自主编程闭环母体（模块化重构版，借鉴 mini-swe-agent 架构）。

模块划分：
    config      配置（pydantic）：AgentConfig / ModelConfig / EnvironmentConfig + 提示词模板
    model       LLM 封装：query / format_observation_messages / token 统计
    tools       工具 Schema 与实现：write_file / read_file / execute_code / submit_result
    environment 执行环境：动作分发、超时保护、终止信号识别
    agent       主循环：run/step/query/execute_actions、异常分级、限额、轨迹保存
    trajectory  把轨迹渲染为人类可读的 execution.log
    exceptions  异常分级：FormatError / LimitsExceeded / TimeExceeded / Submitted
"""
from .agent import CodingAgent
from .config import AgentConfig, EnvironmentConfig, ModelConfig
from .environment import LocalEnvironment
from .exceptions import FormatError, LimitsExceeded, Submitted, TimeExceeded
from .model import Model
from .trajectory import render_human_log

__all__ = [
    "CodingAgent", "Model", "LocalEnvironment",
    "AgentConfig", "ModelConfig", "EnvironmentConfig",
    "FormatError", "LimitsExceeded", "TimeExceeded", "Submitted",
    "render_human_log",
]
