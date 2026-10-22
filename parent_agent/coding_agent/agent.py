"""Agent 核心：自主编程闭环主循环。

设计借鉴 mini-swe-agent 的 DefaultAgent：
    run()  ->  while True: step()  （带异常分级与限额保护）
    step() ->  execute_actions(query())
异常处理：FormatError（连续上限后结束）、LimitsExceeded、TimeExceeded、Submitted（正常结束）
每次 step 后把完整轨迹序列化到 output_path，便于复盘与交付。
"""
from __future__ import annotations

import time
from pathlib import Path

from jinja2 import StrictUndefined, Template

from .config import AgentConfig
from .environment import LocalEnvironment
from .exceptions import FormatError, LimitsExceeded, Submitted, TimeExceeded
from .model import Model


class CodingAgent:
    def __init__(self, model: Model, env: LocalEnvironment, *,
                 config_class: type = AgentConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.model = model
        self.env = env
        self.messages: list[dict] = []
        self.n_consecutive_format_errors = 0
        self._start_time = time.time()

    # ------------------------------------------------------------------
    # 模板渲染
    # ------------------------------------------------------------------
    def get_template_vars(self, **kwargs) -> dict:
        return {
            **self.config.model_dump(),
            **self.model.get_template_vars(),
            "elapsed_seconds": int(time.time() - self._start_time),
            **kwargs,
        }

    def _render(self, template: str) -> str:
        return Template(template, undefined=StrictUndefined).render(**self.get_template_vars())

    def add_messages(self, *messages: dict) -> list[dict]:
        self.messages.extend(messages)
        return list(messages)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(self, task: str = "", **kwargs) -> dict:
        self.messages = []
        self.add_messages(
            self.model.format_message("system", self._render(self.config.system_template)),
            self.model.format_message("user", self._render_instance(task)),
        )
        while True:
            try:
                self.step()
                self.n_consecutive_format_errors = 0
            except FormatError as e:
                self.cost_inc(e.messages[0].get("extra", {}).get("cost", 0.0))
                self.n_consecutive_format_errors += 1
                if (0 < self.config.max_consecutive_format_errors
                        <= self.n_consecutive_format_errors):
                    self.add_messages(*e.messages, self.model.format_message(
                        "exit", "RepeatedFormatError",
                        {"exit_status": "RepeatedFormatError", "submission": ""}))
                else:
                    self.add_messages(*e.messages)
            except (LimitsExceeded, TimeExceeded) as e:
                self.add_messages(e.message)
            except Submitted as e:
                self.add_messages(e.message)
            except Exception as e:  # noqa: BLE001
                self.add_messages(self.model.format_message(
                    "exit", str(e),
                    {"exit_status": type(e).__name__, "exception": str(e)}))
                raise
            finally:
                self.save(Path(self.config.output_path) if self.config.output_path else None)
            if self.messages[-1].get("role") == "exit":
                break
        return self.messages[-1].get("extra", {})

    def _render_instance(self, task: str) -> str:
        return Template(self.config.instance_template, undefined=StrictUndefined).render(
            task=task, **{k: v for k, v in self.get_template_vars().items() if k != "task"})

    def cost_inc(self, c: float):
        self.model.cost += c

    # ------------------------------------------------------------------
    # 单步
    # ------------------------------------------------------------------
    def step(self) -> list[dict]:
        return self.execute_actions(self.query())

    def query(self) -> dict:
        if 0 < self.config.step_limit <= self.model.n_calls:
            raise LimitsExceeded(self.model.format_message(
                "exit", "LimitsExceeded", {"exit_status": "LimitsExceeded", "submission": ""}))
        if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
            raise TimeExceeded(self.model.format_message(
                "exit", "TimeExceeded", {"exit_status": "TimeExceeded", "submission": ""}))
        message = self.model.query(self.messages)
        return self.add_messages(message)[0]

    def execute_actions(self, message: dict) -> list[dict]:
        """解析 assistant 消息中的工具调用 -> 执行 -> 构造观测消息 -> 回灌。"""
        tool_calls = message.get("tool_calls", [])
        if not tool_calls:
            raise FormatError([self.model.format_message(
                "user",
                "你的回复必须至少包含一个工具调用（write_file / read_file / execute_code / submit_result）。"
                        "请立即调用工具推进任务。",
                {"interrupt_type": "FormatError"})])
        actions = []
        for tc in tool_calls:
            try:
                args = __import__("json").loads(tc.get("arguments") or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            actions.append({"name": tc["name"], "arguments": args, "tool_call_id": tc.get("id")})
        outputs = [self.env.execute(a) for a in actions]
        observations = self.model.format_observation_messages(actions, outputs)
        return self.add_messages(*observations)

    # ------------------------------------------------------------------
    # 轨迹保存
    # ------------------------------------------------------------------
    def serialize(self) -> dict:
        last_extra = self.messages[-1].get("extra", {}) if self.messages else {}
        return {
            "info": {
                "model_stats": {"api_calls": self.model.n_calls, "total_tokens": int(self.model.cost)},
                "config": {
                    "agent": self.config.model_dump(mode="json"),
                    "agent_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
                "exit_status": last_extra.get("exit_status", ""),
                "submission": last_extra.get("submission", ""),
            },
            "messages": self.messages,
            "trajectory_format": "coding-agent-1.0",
        }

    def save(self, path: Path | None) -> dict:
        data = self.serialize()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(__import__("json").dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
