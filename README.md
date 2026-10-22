# Coding Agent × Video Agent —— 自主编程闭环实操

本仓库是「极简母体繁育子体」技术实操的落地产物：用一个最小的 **Coding Agent（母体 / Agent 1）**
自主编写出 **Video Agent（子体 / Agent 2）** 并完成真实视频剪辑。

## 目录结构

```
agent_project/
├── parent_agent/              # Agent 1：Coding Agent（母体，自主编程闭环）
│   ├── coding_agent.py        #   入口 CLI
│   ├── coding_agent/          #   模块化实现（config/model/tools/environment/agent/trajectory/exceptions）
│   ├── config.json            #   本地 LLM 配置（含 api_key，已 gitignore）
│   └── config.example.json    #   配置样例（不含密钥）
├── child_agent/               # Agent 2：Video Agent（子体，由母体生成）
│   └── video_agent.py
├── requirements.txt           # 依赖
├── logs/
│   ├── trajectory.json        # 结构化执行轨迹（故障自愈阶段，可完整复盘每一轮）
│   ├── generation_trajectory.json  # 结构化执行轨迹（子体生成阶段）
│   └── execution.log          # 人类可读执行日志（需求→生成→修复→验证→成片 全链路）
├── material/                  # 现场素材（clip_a/b/c.mp4 + logo.png）—— 子体读取
├── output/                    # 成片 v1_output.mp4 / v2_output.mp4 —— 子体写出
├── material_analysis.json     # 素材元数据报告
├── edit_v1.json / edit_v2.json# 两轮剪辑指令
├── timeline_v1.json / timeline_v2.json  # 两次剪辑时间线
├── task_phase2.txt / task_repair.txt    # 母体任务指令（生成 / 故障自愈）
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
│           交叉淡化转场 / 片头片尾卡片(图片段) / 背景音乐混音    │
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
# 从项目根目录运行；母体会把子体写到 child_agent/video_agent.py
python parent_agent/coding_agent.py --task-file task_phase2.txt --max-rounds 40
# 完成后生成 child_agent/video_agent.py；轨迹 logs/trajectory.json、日志 logs/execution.log
```

### 4. 子体实操
```bash
# 素材元数据提取
python child_agent/video_agent.py analyze -i material/clip_a.mp4 -o material_analysis.json

# 首轮剪辑（区间裁剪 + 拼接 + 淡入淡出 + 水印），导出成片与时间线
python child_agent/video_agent.py edit -i edit_v1.json -t timeline_v1.json

# 二次增量修改（开头 1.5x 变速 + 结尾 2s 黑屏淡出）
python child_agent/video_agent.py edit -i edit_v2.json -t timeline_v2.json
```

## 设计要点（对照参考实现 mini-swe-agent）

- **三层分离**：`Model`（LLM）/`Environment`（执行）/`Agent`（调度）各司其职，配置全部走 pydantic。
- **闭环自修复**：`step = execute_actions(query())`，工具执行结果（含 `returncode` 与报错堆栈）回灌模型，驱动自动修复。
- **异常分级**：`FormatError`（连续上限后结束）、`LimitsExceeded`、`TimeExceeded`、`Submitted`（正常终止）。
- **轨迹可复核**：每步把完整消息历史序列化到 `trajectory.json`，并渲染为清晰可读的 `execution.log`。
- **限额与成本感知**：step 上限、墙钟上限、token 统计，运行全程透明。

## 开源协议

本项目采用 **GNU Affero General Public License v3.0（AGPL-3.0-or-later）** 授权。

- 许可证全文见 [`LICENSE`](LICENSE)；各源码文件头部均带 `SPDX-License-Identifier: AGPL-3.0-or-later` 标识。
- 你可以自由地运行、研究、修改与分发本软件，但分发衍生作品时须同样以 AGPLv3 开源，并保留版权与许可证声明。
- **AGPL 第 13 条（网络交互）**：若将修改后的版本作为网络服务提供给他人使用，必须向使用者提供对应的完整源代码。
- 本软件按“原样”提供，不附带任何明示或默示担保；详见许可证正文。

Copyright (C) 2026 Agent Project Contributors
