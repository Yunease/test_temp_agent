"""模型层（Model）：对 OpenAI 兼容 LLM 的封装。

负责：发起请求、解析工具调用、统计 token/调用次数、构造观测消息、提供模板变量。
借鉴 mini-swe-agent 的 Model 抽象（query / format_message / format_observation_messages）。
"""
from __future__ import annotations

from openai import OpenAI

from .config import ModelConfig
from .tools import TOOLS

# 名义单价（美元 / 1K token），仅用于估算；真实计费以平台为准。
NOMINAL_PRICE_PER_1K = 0.0005


class Model:
    def __init__(self, *, config_class: type = ModelConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key, timeout=300)
        self.n_calls = 0
        self.cost = 0.0  # 以 token 计的成本估算（=累计 total_tokens）

    # ------------------------------------------------------------------
    # 与 LLM 交互
    # ------------------------------------------------------------------
    def query(self, messages: list[dict]) -> dict:
        """向模型发起一次请求，返回归一化的 assistant 消息 dict。"""
        api_messages = [self._to_api(m) for m in messages]
        resp = self.client.chat.completions.create(
            model=self.config.model,
            messages=api_messages,
            tools=TOOLS,
            temperature=self.config.temperature,
        )
        self.n_calls += 1
        usage = resp.usage
        total = (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0)
        self.cost += total
        msg = resp.choices[0].message
        tool_calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (msg.tool_calls or [])
        ]
        return {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": tool_calls,
            "extra": {"cost": total, "api_calls": 1, "total_tokens": total},
        }

    @staticmethod
    def _to_api(message: dict) -> dict:
        """去掉 internal 字段（extra 等），转为 OpenAI 接受的消息格式。"""
        m = {}
        for k in ("role", "content", "tool_calls", "tool_call_id", "name"):
            if k in message and message[k] is not None:
                m[k] = message[k]
        if isinstance(m.get("tool_calls"), list):
            m["tool_calls"] = [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in m["tool_calls"]
            ]
        return m

    # ------------------------------------------------------------------
    # 消息构造
    # ------------------------------------------------------------------
    def format_message(self, role: str, content: str, extra: dict | None = None) -> dict:
        msg = {"role": role, "content": content}
        if extra:
            msg["extra"] = extra
        return msg

    def format_observation_messages(self, actions: list[dict], outputs: list[dict]) -> list[dict]:
        """把工具执行结果构造成 tool 角色消息回灌模型。"""
        observations = []
        for action, output in zip(actions, outputs):
            rc = output.get("returncode")
            status = "成功" if rc == 0 else f"失败(returncode={rc})"
            body = output.get("output", "") or ""
            if len(body) > 24000:
                body = body[:24000] + f"\n...[输出过长已截断，共 {len(body)} 字符]"
            text = f"[工具 {action.get('name')} 执行{status}]\n{body}"
            if output.get("exception_info"):
                text += f"\n[异常信息] {output['exception_info']}"
            msg = {"role": "tool", "content": text,
                   "extra": {"returncode": rc, "name": action.get("name")}}
            if action.get("tool_call_id"):
                msg["tool_call_id"] = action["tool_call_id"]
            observations.append(msg)
        return observations

    # ------------------------------------------------------------------
    # 模板变量 / 序列化
    # ------------------------------------------------------------------
    def get_template_vars(self, **kwargs) -> dict:
        return {"model": self.config.model, "n_model_calls": self.n_calls,
                "model_cost": self.cost, **kwargs}

    def serialize(self) -> dict:
        return {
            "info": {
                "model_stats": {"api_calls": self.n_calls, "total_tokens": int(self.cost)},
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }
