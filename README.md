# Coding Agent × Video Agent —— 自主编程闭环实操

本仓库是「极简母体繁育子体」技术实操的落地产物：用一个最小的 **Coding Agent（母体 / Agent 1）**
自主编写出 **Video Agent（子体 / Agent 2）** 并完成真实视频剪辑。

## 目录结构

```
agent_project/
├── coding_agent.py            # Agent 1 入口（CLI）
├── coding_agent/              # Agent 1 模块化实现
│   ├── config.py              #   pydantic 配置 + 提示词模板
│   ├── model.py               #   LLM 封装（OpenAI 兼容） + token 统计
│   ├── tools.py               #   工具 Schema：write_file / read_file / execute_code / submit_result
│   ├── environment.py         #   执行环境：动作分发、超时保护、终止信号
│   ├── agent.py               #   主循环：run/step/query/execute_actions + 异常分级 + 轨迹保存
│   ├── trajectory.py          #   把轨迹渲染为人类可读的 execution.log
│   └── exceptions.py          #   异常分级：FormatError / LimitsExceeded / TimeExceeded / Submitted
├── video_agent.py             # Agent 2：由母体自主生成的视频剪辑 Agent
├── config.json                # 本地 LLM 配置（含 api_key，已 gitignore）
├── config.example.json        # 配置样例（不含密钥）
├── requirements.txt           # 依赖
├── logs/
│   ├── trajectory.json        # 结构化执行轨迹（故障自愈阶段，可完整复盘每一轮）
│   ├── generation_trajectory.json  # 结构化执行轨迹（子体生成阶段）
│   └── execution.log          # 人类可读执行日志（需求→生成→修复→验证→成片 全链路）
├── material/                  # 现场素材（clip_a/b/c.mp4 + logo.png）
├── output/                    # 成片 v1_output.mp4 / v2_output.mp4
├── material_analysis.json     # 素材元数据报告
├── timeline_v1.json           # 首轮剪辑时间线
├── timeline_v2.json           # 二次修改时间线
├── git_history.txt            # Git 阶段演进历史
└── REPORT.md                  # 一页纸说明（架构 / 消耗 / 局限）
```

## 架构（母体 → 子体）

```
┌─────────────── Agent 1 : Coding Agent（母体）───────────────┐
│  接收指令 → LLM 决策(Tool Call) → 执行工具 → 读取结果/报错  │
│      ↑__________________________________修复重写 ← 失败    │
│  工具: write_file / read_file / execute_code / submit_result  │
└──────────────────────────────┬─────────────────────────────┘
                                │ 自主编写并调通
                                ▼
┌─────────────── Agent 2 : Video Agent（子体）───────────────┐
│  analyze: ffprobe 提取元数据 → material_analysis.json        │
│  edit:    读 timeline(JSON) → 拼装 ffmpeg → 导出 mp4         │
│           支持 区间裁剪 / 多段拼接 / 淡入淡出 / 文字水印 / 变速│
│           增量修改：基于上一版 timeline 推导新 timeline         │
└─────────────────────────────────────────────────────────────┘
```

## 快速启动

### 1. 准备环境
```bash
pip install -r requirements.txt        # openai / jinja2 / pydantic / requests
# 确保本机已安装 ffmpeg / ffprobe 并加入 PATH
```

### 2. 配置 LLM（OpenAI 兼容接口）
复制 `config.example.json` 为 `config.json`，填入你的 `base_url` / `api_key` / `model`；
或用环境变量覆盖：`CODING_AGENT_API_KEY` / `CODING_AGENT_BASE_URL` / `CODING_AGENT_MODEL`。

### 3. 运行母体，自主生成子体（阶段二）
```bash
python coding_agent.py --task-file task_phase2.txt --max-rounds 40
# 完成后生成 video_agent.py；轨迹 logs/trajectory.json、日志 logs/execution.log
```

### 4. 子体实操
```bash
# 素材元数据提取
python video_agent.py analyze -i material/clip_a.mp4 -o material_analysis.json

# 首轮剪辑（区间裁剪 + 拼接 + 淡入淡出 + 水印），导出成片与时间线
python video_agent.py edit -i edit_v1.json -t timeline_v1.json

# 二次增量修改（开头 1.5x 变速 + 结尾 2s 黑屏淡出）
python video_agent.py edit -i edit_v2.json -t timeline_v2.json
```

## 设计要点（对照参考实现 mini-swe-agent）

- **三层分离**：`Model`（LLM）/`Environment`（执行）/`Agent`（调度）各司其职，配置全部走 pydantic。
- **闭环自修复**：`step = execute_actions(query())`，工具执行结果（含 `returncode` 与报错堆栈）回灌模型，驱动自动修复。
- **异常分级**：`FormatError`（连续上限后结束）、`LimitsExceeded`、`TimeExceeded`、`Submitted`（正常终止）。
- **轨迹可复核**：每步把完整消息历史序列化到 `trajectory.json`，并渲染为清晰可读的 `execution.log`。
- **限额与成本感知**：step 上限、墙钟上限、token 统计，运行全程透明。
