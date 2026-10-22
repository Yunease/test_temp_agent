# SPDX-FileCopyrightText: 2026 Agent Project Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""环境层（Environment）：负责执行动作并产出观测结果。

借鉴 mini-swe-agent 的 LocalEnvironment：执行命令返回结构化输出、
在超时/异常时不留孤儿进程、提供模板变量与序列化信息。
本层还负责把动作分发给 tools 层并识别 submit 终止信号。
"""
from __future__ import annotations

from pathlib import Path

from .config import EnvironmentConfig
from .exceptions import Submitted
from .tools import SUBMIT_TOOL, run_action


class LocalEnvironment:
    def __init__(self, *, config_class: type = EnvironmentConfig, root: Path | None = None, **kwargs):
        self.config = config_class(**kwargs)
        self.root = (root or Path(self.config.cwd)).resolve()

    def execute(self, action: dict) -> dict:
        """执行动作；若动作为 submit_result，则抛 Submitted 结束运行。"""
        if action.get("name") == SUBMIT_TOOL:
            raise Submitted({
                "role": "exit",
                "content": action.get("arguments", {}).get("summary", ""),
                "extra": {"exit_status": "Submitted", "submission": action.get("arguments", {}).get("summary", "")},
            })
        return run_action(action, self.root)

    def get_template_vars(self, **kwargs) -> dict:
        return {**self.config.model_dump(), "cwd": str(self.root), **kwargs}

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }
