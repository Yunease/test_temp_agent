#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_agent.py
==============
一个基于 FFmpeg/FFprobe 的视频剪辑 Agent。

功能：
- analyze：提取视频元数据并输出 JSON 报告。
- edit：根据 JSON 剪辑指令执行裁剪、拼接、淡入淡出、文字水印、变速等操作，
        导出 mp4 并生成剪辑时间线 timeline JSON。
- --test：自检模式，验证环境及核心函数可用性。

设计原则：
- 优先直接调用 ffmpeg/ffprobe 子进程，不依赖 moviepy 等重型库。
- 所有命令行参数、注释、输出均为中文，便于国内用户使用。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 常量与配置
# ---------------------------------------------------------------------------

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_PRESET = "medium"
DEFAULT_CRF = "23"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def run_command(cmd: List[str], check: bool = True, capture: bool = True) -> Tuple[int, str, str]:
    """
    执行外部命令并返回 (returncode, stdout, stderr)。
    """
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"命令执行失败 (returncode={result.returncode}): {' '.join(cmd)}\n{result.stderr}"
            )
        return result.returncode, result.stdout or "", result.stderr or ""
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到可执行文件: {cmd[0]}") from exc


def check_ffmpeg_tools() -> Dict[str, bool]:
    """
    检查 ffmpeg 与 ffprobe 是否在 PATH 中可用。
    """
    tools = {}
    for tool in ["ffmpeg", "ffprobe"]:
        path = shutil.which(tool)
        tools[tool] = path is not None
    return tools


# ---------------------------------------------------------------------------
# 元数据提取模块
# ---------------------------------------------------------------------------

def probe_video(video_path: str) -> Dict[str, Any]:
    """
    使用 ffprobe 提取视频元数据。

    返回包含以下字段的字典：
    - duration: 时长（秒）
    - width, height: 分辨率
    - codec_name: 视频编码格式
    - pix_fmt: 像素格式
    - frame_rate: 帧率（浮点数）
    - bit_rate: 码率（bps）
    - audio_streams: 音轨信息列表
    - format_name: 容器格式
    - size: 文件大小（字节）
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    _, stdout, _ = run_command(cmd)
    data = json.loads(stdout)

    video_stream = None
    audio_streams = []
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio":
            audio_streams.append(stream)

    if video_stream is None:
        raise ValueError(f"无法从视频中找到视频流: {video_path}")

    # 计算帧率
    r_frame_rate = video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = r_frame_rate.split("/")
        frame_rate = float(num) / float(den) if float(den) != 0 else 0.0
    except (ValueError, ZeroDivisionError):
        frame_rate = 0.0

    # 计算时长
    try:
        duration = float(video_stream.get("duration", data.get("format", {}).get("duration", 0)))
    except (ValueError, TypeError):
        duration = 0.0

    meta = {
        "file_path": os.path.abspath(video_path),
        "duration": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "codec_name": video_stream.get("codec_name"),
        "pix_fmt": video_stream.get("pix_fmt"),
        "frame_rate": round(frame_rate, 3),
        "bit_rate": int(video_stream.get("bit_rate", 0) or 0),
        "audio_streams": [
            {
                "index": s.get("index"),
                "codec_name": s.get("codec_name"),
                "sample_rate": s.get("sample_rate"),
                "channels": s.get("channels"),
                "channel_layout": s.get("channel_layout"),
            }
            for s in audio_streams
        ],
        "format_name": data.get("format", {}).get("format_name"),
        "size": int(data.get("format", {}).get("size", 0) or 0),
    }
    return meta


def analyze_command(args: argparse.Namespace) -> int:
    """
    analyze 子命令：提取视频元数据并输出 JSON 报告。
    """
    video_path = args.input
    meta = probe_video(video_path)

    output_path = args.output
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"元数据报告已保存至: {output_path}")
    else:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# 剪辑指令与时间线数据结构
# ---------------------------------------------------------------------------

@dataclass
class ClipSegment:
    """表示一段素材裁剪区间。"""
    source: str
    start: float
    end: float
    speed: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    text_watermarks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EditInstruction:
    """剪辑指令整体结构。"""
    output: str
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    video_codec: str = DEFAULT_VIDEO_CODEC
    audio_codec: str = DEFAULT_AUDIO_CODEC
    preset: str = DEFAULT_PRESET
    crf: str = DEFAULT_CRF
    segments: List[ClipSegment] = field(default_factory=list)
    base_timeline: Optional[str] = None


def parse_edit_json(json_path: str) -> EditInstruction:
    """
    解析剪辑指令 JSON 文件。

    JSON 示例：
    {
      "output": "output.mp4",
      "width": 1920,
      "height": 1080,
      "fps": 30,
      "video_codec": "libx264",
      "audio_codec": "aac",
      "preset": "medium",
      "crf": "23",
      "base_timeline": "timeline.json",  // 可选，读取上次 timeline 做增量修改
      "segments": [
        {
          "source": "a.mp4",
          "start": 0,
          "end": 5,
          "speed": 1.0,
          "fade_in": 0.5,
          "fade_out": 0.5,
          "text_watermarks": [
            {
              "text": "示例水印",
              "x": 10,
              "y": 10,
              "fontsize": 24,
              "fontcolor": "white",
              "enable_start": 0,
              "enable_end": 5
            }
          ]
        }
      ]
    }
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = []
    for seg_data in data.get("segments", []):
        segments.append(ClipSegment(
            source=seg_data["source"],
            start=float(seg_data.get("start", 0)),
            end=float(seg_data.get("end", 0)),
            speed=float(seg_data.get("speed", 1.0)),
            fade_in=float(seg_data.get("fade_in", 0.0)),
            fade_out=float(seg_data.get("fade_out", 0.0)),
            text_watermarks=seg_data.get("text_watermarks", []),
        ))

    return EditInstruction(
        output=data["output"],
        width=data.get("width"),
        height=data.get("height"),
        fps=data.get("fps"),
        video_codec=data.get("video_codec", DEFAULT_VIDEO_CODEC),
        audio_codec=data.get("audio_codec", DEFAULT_AUDIO_CODEC),
        preset=data.get("preset", DEFAULT_PRESET),
        crf=data.get("crf", DEFAULT_CRF),
        segments=segments,
        base_timeline=data.get("base_timeline"),
    )


# ---------------------------------------------------------------------------
# 视频处理模块
# ---------------------------------------------------------------------------

def _escape_text(text: str) -> str:
    """
    对 drawtext 滤镜中的文本进行转义。
    """
    # 先替换反斜杠、单引号、冒号等特殊字符
    text = text.replace("\\", "\\\\").replace("'", "'\\''")
    return text


# 各平台常见的中文字体候选路径（按优先级探测）
_CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",      # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",    # Windows 黑体
    "C:/Windows/Fonts/simsun.ttc",    # Windows 宋体
    "/System/Library/Fonts/PingFang.ttc",   # macOS 苹方
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux Noto CJK
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",          # Linux 文泉驿
]


def _find_cjk_font() -> Optional[str]:
    """
    探测本机可用的中文字体文件路径；找不到返回 None（由 ffmpeg 使用默认字体）。
    """
    for path in _CJK_FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def build_segment_filter(segment: ClipSegment, width: int, height: int, fps: float) -> str:
    """
    为单个片段构建 ffmpeg 视频滤镜链。

    支持：裁剪区间、变速、淡入淡出、文字水印。
    """
    filters = []

    # 1. 裁剪时间区间
    duration = segment.end - segment.start
    filters.append(f"trim=start={segment.start}:end={segment.end},setpts=PTS-STARTPTS")

    # 2. 变速（setpts=1/speed*PTS）
    if segment.speed != 1.0 and segment.speed > 0:
        filters.append(f"setpts={1.0 / segment.speed:.4f}*PTS")
        duration = duration / segment.speed

    # 3. 缩放与帧率统一
    filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")

    # 4. 淡入淡出
    if segment.fade_in > 0:
        filters.append(f"fade=t=in:st=0:d={segment.fade_in}")
    if segment.fade_out > 0:
        filters.append(f"fade=t=out:st={max(0, duration - segment.fade_out)}:d={segment.fade_out}")

    # 5. 文字水印
    for wm in segment.text_watermarks:
        text = _escape_text(wm.get("text", ""))
        x = wm.get("x", 10)
        y = wm.get("y", 10)
        fontsize = wm.get("fontsize", 24)
        fontcolor = wm.get("fontcolor", "white")
        enable_start = wm.get("enable_start", 0)
        enable_end = wm.get("enable_end", duration)
        # 自动选择支持中文的字体：优先使用指令中的 fontfile，否则在常见系统中探测 CJK 字体
        # 注意：Windows 盘符冒号需双重转义（滤镜图引号剥离后，drawtext 选项层仍会按 ':' 分隔）
        fontfile = wm.get("fontfile") or _find_cjk_font()
        if fontfile:
            font_path = _escape_text(fontfile).replace(":", "\\:")
            font_part = f"fontfile='{font_path}':"
        else:
            font_part = ""
        filters.append(
            f"drawtext={font_part}text='{text}':x={x}:y={y}:fontsize={fontsize}:fontcolor={fontcolor}:"
            f"enable='between(t,{enable_start},{enable_end})'"
        )

    return ",".join(filters)


def process_segment(segment: ClipSegment, width: int, height: int, fps: float,
                    temp_dir: str, index: int) -> str:
    """
    处理单个片段，输出中间文件，返回中间文件路径。
    """
    output_path = os.path.join(temp_dir, f"segment_{index:04d}.mp4")
    vf = build_segment_filter(segment, width, height, fps)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", segment.source,
        "-vf", vf,
        "-af", f"atrim=start={segment.start}:end={segment.end},asetpts=PTS-STARTPTS",
        "-c:v", DEFAULT_VIDEO_CODEC,
        "-preset", DEFAULT_PRESET,
        "-crf", DEFAULT_CRF,
        "-c:a", DEFAULT_AUDIO_CODEC,
        "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts",
        output_path,
    ]
    run_command(cmd)
    return output_path


def concatenate_segments(segment_files: List[str], output_path: str,
                         video_codec: str, audio_codec: str,
                         preset: str, crf: str) -> None:
    """
    使用 ffmpeg concat demuxer 拼接多个片段。
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        concat_file = f.name
        for seg in segment_files:
            f.write(f"file '{os.path.abspath(seg)}'\n")

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c:v", video_codec,
            "-preset", preset,
            "-crf", crf,
            "-c:a", audio_codec,
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
        run_command(cmd)
    finally:
        try:
            os.remove(concat_file)
        except OSError:
            pass


def edit_command(args: argparse.Namespace) -> int:
    """
    edit 子命令：解析剪辑指令，渲染输出 mp4 并导出 timeline JSON。
    """
    instruction_path = args.instruction
    instruction = parse_edit_json(instruction_path)

    # 若指定了 base_timeline，则读取并合并（增量修改）
    base_segments: List[ClipSegment] = []
    if instruction.base_timeline and os.path.isfile(instruction.base_timeline):
        with open(instruction.base_timeline, "r", encoding="utf-8") as f:
            base_data = json.load(f)
        for seg_data in base_data.get("segments", []):
            base_segments.append(ClipSegment(
                source=seg_data["source"],
                start=float(seg_data.get("start", 0)),
                end=float(seg_data.get("end", 0)),
                speed=float(seg_data.get("speed", 1.0)),
                fade_in=float(seg_data.get("fade_in", 0.0)),
                fade_out=float(seg_data.get("fade_out", 0.0)),
                text_watermarks=seg_data.get("text_watermarks", []),
            ))

    all_segments = base_segments + instruction.segments

    if not all_segments:
        print("错误：没有有效的片段可编辑。", file=sys.stderr)
        return 1

    # 确定输出分辨率与帧率：优先使用第一个素材的元数据
    first_source = all_segments[0].source
    meta = probe_video(first_source)
    width = instruction.width or meta.get("width") or 1920
    height = instruction.height or meta.get("height") or 1080
    fps = instruction.fps or meta.get("frame_rate") or 30.0

    timeline_segments = []
    temp_dir_obj = tempfile.TemporaryDirectory()
    temp_dir = temp_dir_obj.name

    try:
        segment_files = []
        for index, segment in enumerate(all_segments):
            seg_file = process_segment(segment, width, height, fps, temp_dir, index)
            segment_files.append(seg_file)

            timeline_segments.append({
                "index": index,
                "source": segment.source,
                "start": segment.start,
                "end": segment.end,
                "speed": segment.speed,
                "fade_in": segment.fade_in,
                "fade_out": segment.fade_out,
                "text_watermarks": segment.text_watermarks,
                "intermediate_file": seg_file,
            })

        # 拼接
        output_path = instruction.output
        concatenate_segments(
            segment_files,
            output_path,
            instruction.video_codec,
            instruction.audio_codec,
            instruction.preset,
            instruction.crf,
        )

        # 生成 timeline JSON
        timeline = {
            "output": os.path.abspath(output_path),
            "width": width,
            "height": height,
            "fps": fps,
            "video_codec": instruction.video_codec,
            "audio_codec": instruction.audio_codec,
            "preset": instruction.preset,
            "crf": instruction.crf,
            "segments": timeline_segments,
        }
        timeline_path = args.timeline or (os.path.splitext(output_path)[0] + "_timeline.json")
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump(timeline, f, ensure_ascii=False, indent=2)

        print(f"视频已导出: {output_path}")
        print(f"时间线已导出: {timeline_path}")
        return 0
    finally:
        temp_dir_obj.cleanup()


# ---------------------------------------------------------------------------
# 自检模块
# ---------------------------------------------------------------------------

def self_test() -> int:
    """
    自检模式：验证 ffmpeg/ffprobe 可用，并测试核心函数。
    """
    print("开始自检...")

    # 1. 检查工具
    tools = check_ffmpeg_tools()
    if not tools["ffmpeg"]:
        print("错误：未找到 ffmpeg，请检查 PATH 环境变量。", file=sys.stderr)
        return 1
    if not tools["ffprobe"]:
        print("错误：未找到 ffprobe，请检查 PATH 环境变量。", file=sys.stderr)
        return 1
    print("[OK] ffmpeg 与 ffprobe 已找到。")

    # 2. 生成测试视频并验证 analyze
    with tempfile.TemporaryDirectory() as tmpdir:
        test_video = os.path.join(tmpdir, "test.mp4")
        # 生成 2 秒 1280x720 测试视频
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=2:size=1280x720:rate=30",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:duration=2",
            "-pix_fmt", "yuv420p",
            test_video,
        ]
        ret, _, stderr = run_command(cmd)
        if ret != 0:
            print(f"生成测试视频失败: {stderr}", file=sys.stderr)
            return 1
        print("[OK] 测试视频生成成功。")

        # 3. 测试元数据提取
        try:
            meta = probe_video(test_video)
            assert meta["width"] == 1280
            assert meta["height"] == 720
            assert meta["duration"] >= 2.0
            print("[OK] 元数据提取成功。")
        except Exception as exc:
            print(f"[FAIL] 元数据提取失败: {exc}", file=sys.stderr)
            return 1

        # 4. 测试剪辑指令
        edit_json_path = os.path.join(tmpdir, "edit.json")
        output_path = os.path.join(tmpdir, "output.mp4")
        timeline_path = os.path.join(tmpdir, "timeline.json")
        edit_data = {
            "output": output_path,
            "width": 640,
            "height": 360,
            "fps": 30,
            "segments": [
                {
                    "source": test_video,
                    "start": 0.0,
                    "end": 1.0,
                    "speed": 1.0,
                    "fade_in": 0.2,
                    "fade_out": 0.2,
                    "text_watermarks": [
                        {
                            "text": "测试水印",
                            "x": 10,
                            "y": 10,
                            "fontsize": 24,
                            "fontcolor": "red",
                            "enable_start": 0,
                            "enable_end": 1,
                        }
                    ],
                }
            ],
        }
        with open(edit_json_path, "w", encoding="utf-8") as f:
            json.dump(edit_data, f, ensure_ascii=False, indent=2)

        class Args:
            instruction = edit_json_path
            timeline = timeline_path

        ret = edit_command(Args())
        if ret != 0:
            print("[FAIL] 剪辑执行失败。", file=sys.stderr)
            return 1
        if not os.path.isfile(output_path):
            print("[FAIL] 输出视频未生成。", file=sys.stderr)
            return 1
        if not os.path.isfile(timeline_path):
            print("[FAIL] 时间线 JSON 未生成。", file=sys.stderr)
            return 1
        print("[OK] 剪辑执行成功，输出视频与时间线均已生成。")

        # 5. 验证 timeline 增量修改
        with open(timeline_path, "r", encoding="utf-8") as f:
            timeline_data = json.load(f)
        assert "segments" in timeline_data
        assert len(timeline_data["segments"]) == 1
        print("[OK] 时间线 JSON 结构正确。")

    print("自检全部通过。")
    return 0


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """
    构建命令行解析器。
    """
    parser = argparse.ArgumentParser(
        prog="video_agent.py",
        description="基于 FFmpeg/FFprobe 的视频剪辑 Agent。",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用子命令")

    # analyze 子命令
    analyze_parser = subparsers.add_parser("analyze", help="提取视频元数据并输出 JSON 报告")
    analyze_parser.add_argument("-i", "--input", required=True, help="输入视频路径")
    analyze_parser.add_argument("-o", "--output", default=None, help="输出 JSON 报告路径（可选）")

    # edit 子命令
    edit_parser = subparsers.add_parser("edit", help="根据 JSON 剪辑指令渲染视频")
    edit_parser.add_argument("-i", "--instruction", required=True, help="剪辑指令 JSON 文件路径")
    edit_parser.add_argument("-t", "--timeline", default=None, help="输出 timeline JSON 路径（可选）")

    # --test 自检
    parser.add_argument("--test", action="store_true", help="自检模式")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """
    程序入口。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.test:
        return self_test()

    if args.command == "analyze":
        return analyze_command(args)
    elif args.command == "edit":
        return edit_command(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
