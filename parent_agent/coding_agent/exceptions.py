# SPDX-FileCopyrightText: 2026 Agent Project Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent 异常分级（借鉴 mini-swe-agent 的设计）。

- FormatError      模型返回内容无法解析为合法动作（如缺少 tool call / 参数非法）。
- LimitsExceeded   达到 step / cost 上限。
- TimeExceeded     达到墙钟时间上限。
- Submitted        模型显式提交最终结果，结束本轮 Agent 运行。
"""
from __future__ import annotations

from typing import Any


class FormatError(Exception):
    """模型输出格式错误；可携带用于回灌模型的纠正消息。"""

    def __init__(self, messages: list[dict[str, Any]], *, cost: float = 0.0):
        self.messages = messages
        self.messages[0].setdefault("extra", {})["cost"] = cost
        super().__init__(f"FormatError: {self.messages[0].get('content')}")


class LimitsExceeded(Exception):
    """达到 step / cost 限制。"""

    def __init__(self, message: dict[str, Any]):
        self.message = message
        super().__init__("LimitsExceeded")


class TimeExceeded(Exception):
    """达到墙钟时间限制。"""

    def __init__(self, message: dict[str, Any]):
        self.message = message
        super().__init__("TimeExceeded")


class Submitted(Exception):
    """模型通过 submit_result 工具提交最终结论。"""

    def __init__(self, message: dict[str, Any]):
        self.message = message
        super().__init__("Submitted")
