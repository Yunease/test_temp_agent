# 实操交付说明（一页纸）

> 项目：极简母体繁育子体 —— Coding Agent × Video Agent 自主编程闭环
> 日期：2026-10-22 · 环境：Windows / Python 3.13 / ffmpeg 2025-12-24

## 一、做了什么
用一个最小的 **Coding Agent（母体 / Agent 1）** 自主编写出 **Video Agent（子体 / Agent 2）**，
并由子体完成真实视频剪辑（素材分析 → 首轮成片 → 二次增量修改）。全程由代码闭环驱动，
每一步都被结构化轨迹与可读日志完整记录。

## 二、架构（母体 → 子体）
```
┌──────────── Agent 1 : Coding Agent（母体，借鉴 mini-swe-agent）────────────┐
│  Model (LLM)  │  Environment (执行)  │  Agent (调度)                       │
│  query()      │  write_file/read_    │  run(): while step()                 │
│  token 统计   │   file/execute_code  │  step = execute_actions(query())      │
│               │   submit_result      │  异常分级 + 限额 + 轨迹保存           │
│  工具: write_file / read_file / execute_code / submit_result                │
└───────────────────────────────┬───────────────────────────────────────────┘
                                 │ 自主编写并调通（含一次语法故障自愈）
                                 ▼
┌──────────── Agent 2 : Video Agent（子体，由母体生成，22KB）───────────────┐
│  analyze : ffprobe 提取元数据 → material_analysis.json                     │
│  edit    : 读 timeline(JSON) → 拼装 ffmpeg → 导出 mp4 + timeline JSON      │
│           支持 区间裁剪 / 多段拼接 / 淡入淡出 / 文字水印 / 变速             │
│           增量修改：基于上一版 timeline 推导新 timeline                      │
└────────────────────────────────────────────────────────────────────────────┘
```

## 三、阶段产出（对照规划书）
| 阶段 | 产物 |
|---|---|
| 一 母体骨架 | `coding_agent.py` + `coding_agent/` 包（已 Git 提交） |
| 二 繁育子体 | `video_agent.py`（母体生成，含一次语法故障→读取 stderr→修复→`--test` 通过） |
| 三 首轮成片 | `material_analysis.json`、`timeline_v1.json`、`output/v1_output.mp4`（14.1s） |
| 四 二次修改 | `timeline_v2.json`、`output/v2_output.mp4`（16.1s，开头 1.5x + 结尾 2s 黑屏淡出） |
| 五 材料整理 | `README.md`、`requirements.txt`、`git_history.txt`、`execution.log`、`REPORT.md` |

## 四、整体消耗（如实记录）
- **Token：** 两次自主运行合计 **10 次 LLM 调用 / ≈ 99.4k tokens**（生成 3 次 21k；修复 7 次 78k）。
- **耗时：** 实际自主运行（含生成 + 故障自愈 + 剪辑）约 **10 分钟**计算时间；规划预算约 2 小时。
- **费用：** 依赖本机已配置的 OpenAI 兼容中转额度（tokenrhythm / kimi-k2.7-code），
  按 ~99k tokens 折算约 0.1–0.3 美元量级（实际以平台计费为准），属低成本实验。

## 五、已知局限
1. 素材为程序生成的测试片（testsrc / smptebars + 正弦音），非真实拍摄素材。
2. 剪辑基于 **ffmpeg 命令行拼装**（未用 moviepy 重型库）；复杂转场 / 关键帧动画 / 全局调色未覆盖。
3. 自修复演示为**向生成文件注入语法错误后由 Agent 自主修复**（满足规划书"故意保留修复过程"要求），非线上真实故障。
4. `edit` 的 `base_timeline` 为"追加式"增量：修改既有片段需重写整段指令 JSON，非字段级 patch。
5. Coding Agent 的 LLM 依赖外部 OpenAI 兼容接口，网络/额度受限时需替换 `config.json`。
6. 单次编辑为顺序拼接，未做多轨音频混音与字幕烧录以外的后期处理。

## 六、如何复现
```bash
pip install -r requirements.txt          # openai / jinja2 / pydantic / requests
# 配置 LLM：复制 config.example.json 为 config.json 并填入 base_url/api_key/model
python coding_agent.py --task-file task_phase2.txt --max-rounds 40   # 重新繁育子体
python video_agent.py analyze -i material/clip_a.mp4 -o material_analysis.json
python video_agent.py edit -i edit_v1.json -t timeline_v1.json
python video_agent.py edit -i edit_v2.json -t timeline_v2.json
```
