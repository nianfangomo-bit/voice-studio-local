from __future__ import annotations
import subprocess
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import wave
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from datetime import datetime


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Segment:
    index: int
    text: str
    paragraph_break: bool
    wav_path: Path | None = None
    start_ms: int = 0
    end_ms: int = 0


# 中文维护：将相对路径解析到指定基准，保留兼容命令行工作流。
def resolve_path(value: str | Path, base: Path = ROOT) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path


# 中文维护：读取 UTF-8 配置文件并返回参数字典。
def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    return config


# 中文维护：保留标点并按段落与句末切分，记录段落间隔标识。
def split_script(text: str) -> list[Segment]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    segments: list[Segment] = []
    index = 1

    for paragraph_index, paragraph in enumerate(paragraphs):
        parts = re.split(r"(?<=[。！？!?；;])\s*|(?<=\.)\s+", paragraph)
        parts = [part.strip() for part in parts if part and part.strip()]
        for part_index, part in enumerate(parts):
            paragraph_break = paragraph_index > 0 and part_index == 0
            segments.append(Segment(index=index, text=part, paragraph_break=paragraph_break))
            index += 1

    return segments


# 中文维护：调用旧 TTS HTTP 服务并验证返回 WAV，JSON 错误不能当作音频。
def post_tts(api_url: str, payload: dict[str, Any], out_path: Path) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GPT-SoVITS API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Cannot connect to GPT-SoVITS API. Start api_v2.py first, then run this tool again."
        ) from exc

    if "json" in content_type.lower():
        detail = data.decode("utf-8", errors="replace")
        raise RuntimeError(f"GPT-SoVITS returned an error response: {detail}")

    out_path.write_bytes(data)
    ensure_wav(out_path)


# 中文维护：尝试解析 WAV 头部，格式错误时给出明确失败。
def ensure_wav(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as wav:
            wav.getparams()
    except wave.Error as exc:
        raise RuntimeError(f"{path.name} is not a valid wav file. Check media_type and streaming_mode.") from exc


# 中文维护：从整数 PCM 采样计算归一化 RMS 和峰值，兼容不同采样宽度。
def _pcm_rms_and_peak(frames: bytes, sample_width: int) -> tuple[float, float]:
    """Return normalized RMS and peak for integer PCM samples."""
    if sample_width not in (1, 2, 3, 4):
        raise RuntimeError(f"unsupported PCM sample width: {sample_width}")
    sample_count = len(frames) // sample_width
    if sample_count == 0:
        return 0.0, 0.0

    full_scale = float(1 << (sample_width * 8 - 1))
    square_sum = 0.0
    peak = 0
    for offset in range(0, sample_count * sample_width, sample_width):
        raw = frames[offset:offset + sample_width]
        if sample_width == 1:
            value = raw[0] - 128
        else:
            value = int.from_bytes(raw, "little", signed=True)
        magnitude = abs(value)
        peak = max(peak, magnitude)
        square_sum += float(value) * value
    return math.sqrt(square_sum / sample_count) / full_scale, peak / full_scale


# 中文维护：给出保守的异常短音频门槛，不将此启发式当作漏读识别。
def minimum_plausible_duration_ms(text: str) -> int:
    # Deliberately conservative: reject tiny placeholder responses without
    # penalising a naturally fast voice or short interjections.
    spoken_chars = len(re.sub(r"\s|[^\w\u4e00-\u9fff]", "", text))
    return max(450, min(2400, spoken_chars * 55))


# 中文维护：检查 PCM 参数、时长和近静音，返回实际测量值。
def validate_segment_wav(path: Path, text: str) -> dict[str, float | int]:
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            frame_rate = wav.getframerate()
            frame_count = wav.getnframes()
            compression = wav.getcomptype()
            frames = wav.readframes(frame_count)
    except (OSError, EOFError, wave.Error) as exc:
        raise RuntimeError("WAV 文件损坏或无法读取") from exc

    if channels < 1 or frame_rate <= 0 or frame_count <= 0 or compression != "NONE":
        raise RuntimeError("WAV 参数无效或不是未压缩 PCM")

    duration_ms = round(frame_count * 1000 / frame_rate)
    expected_min_ms = minimum_plausible_duration_ms(text)
    if duration_ms < expected_min_ms:
        raise RuntimeError(
            f"音频时长异常（{duration_ms}ms，按文本长度至少应约 {expected_min_ms}ms）"
        )

    rms, peak = _pcm_rms_and_peak(frames, sample_width)
    if rms < 0.0015 or peak < 0.006:
        raise RuntimeError(f"音频接近静音（RMS={rms:.5f}, peak={peak:.5f}）")

    return {"duration_ms": duration_ms, "rms": rms, "peak": peak}


# 中文维护：使用临时文件有限重试，仅将验证通过的音频替换为目标结果。
def generate_segment_with_retry(
    api_url: str,
    payload: dict[str, Any],
    segment: Segment,
    out_path: Path,
    max_retries: int = 2,
    backoff_seconds: float = 0.8,
    retry_seeds: list[int] | None = None,
) -> None:
    last_error: Exception | None = None
    attempt_path = out_path.with_name(out_path.stem + ".__attempt.wav")
    for attempt in range(max_retries + 1):
        try:
            attempt_path.unlink(missing_ok=True)
            attempt_payload = dict(payload)
            if retry_seeds:
                attempt_payload["seed"] = retry_seeds[attempt % len(retry_seeds)]
            post_tts(api_url, attempt_payload, attempt_path)
            validate_segment_wav(attempt_path, segment.text)
            attempt_path.replace(out_path)
            return
        except Exception as exc:
            last_error = exc
            attempt_path.unlink(missing_ok=True)
            if attempt < max_retries:
                retry_number = attempt + 1
                print(
                    f"第 {segment.index} 句生成异常，正在重试 {retry_number}/{max_retries}：{exc}",
                    flush=True,
                )
                time.sleep(backoff_seconds * retry_number)

    raise RuntimeError(f"第 {segment.index} 句生成失败：{segment.text}（{last_error}）")


# 中文维护：依据实际采样帧数计算毫秒时长。
def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
    return round(frames * 1000 / rate)


# 中文维护：按目标 PCM 格式创建指定时长的零采样间隔。
def silence_frames(params: wave._wave_params, duration_ms: int) -> bytes:
    if duration_ms <= 0:
        return b""
    frame_count = round(params.framerate * duration_ms / 1000)
    return b"\x00" * frame_count * params.nchannels * params.sampwidth


# 中文维护：仅对调用者指定的任务音频统一采样率与声道，经临时文件替换。
def normalize_wav(path: Path) -> None:
    temp_path = path.with_name(path.stem + ".__normalized.wav")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-i", str(path),
            "-ac", "1",
            "-ar", "32000",
            "-c:a", "pcm_s16le",
            str(temp_path),
        ],
        check=True,
    )

    temp_path.replace(path)





# 中文维护：统一片段格式后插入停顿并合并，回填每段真实起止时间。
def combine_wavs(segments: list[Segment], output_path: Path, sentence_gap_ms: int, paragraph_gap_ms: int) -> None:
    if not segments:
        raise RuntimeError("No segments to combine.")
    # 先统一所有分段 WAV 参数
    for segment in segments:
        if segment.wav_path is not None:
            normalize_wav(segment.wav_path)
    first_path = segments[0].wav_path
    if first_path is None:
        raise RuntimeError("First segment has no wav path.")

    with wave.open(str(first_path), "rb") as first:
        params = first.getparams()

    current_ms = 0
    with wave.open(str(output_path), "wb") as out:
        out.setparams(params)

        for segment in segments:
            gap_ms = paragraph_gap_ms if segment.paragraph_break else (sentence_gap_ms if segment.index > 1 else 0)
            if gap_ms:
                out.writeframes(silence_frames(params, gap_ms))
                current_ms += gap_ms

            if segment.wav_path is None:
                raise RuntimeError(f"Segment {segment.index} has no wav path.")

            with wave.open(str(segment.wav_path), "rb") as wav:
                if wav.getparams()[:3] != params[:3]:
                    raise RuntimeError(
                        f"{segment.wav_path.name} uses different wav parameters. "
                        "Regenerate with the same GPT-SoVITS settings."
                    )
                frames = wav.readframes(wav.getnframes())
                duration_ms = round(wav.getnframes() * 1000 / wav.getframerate())

            segment.start_ms = current_ms
            out.writeframes(frames)
            current_ms += duration_ms
            segment.end_ms = current_ms


# 中文维护：将毫秒转换为 SRT 的时分秒与三位毫秒格式。
def fmt_srt_time(ms: int) -> str:
    hours = ms // 3_600_000
    ms %= 3_600_000
    minutes = ms // 60_000
    ms %= 60_000
    seconds = ms // 1000
    millis = ms % 1000
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


# 中文维护：按已计算时间与文本写出 UTF-8 句级字幕。
def write_srt(segments: list[Segment], path: Path) -> None:
    blocks = []
    for segment in segments:
        blocks.append(
            f"{segment.index}\n"
            f"{fmt_srt_time(segment.start_ms)} --> {fmt_srt_time(segment.end_ms)}\n"
            f"{segment.text}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


# 中文维护：写出旧命令行工作流的音频、字幕和分段清单。
def write_manifest(segments: list[Segment], path: Path, full_wav: Path, srt_path: Path) -> None:
    data = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "full_wav": str(full_wav),
        "srt": str(srt_path),
        "segments": [
            {
                "index": segment.index,
                "text": segment.text,
                "wav": str(segment.wav_path),
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
            }
            for segment in segments
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 中文维护：为旧 API 解析参考音频本机路径。
def resolve_audio_path_for_api(value: str) -> str:
    return str(resolve_path(value))


# 中文维护：组合参考音频、语言和生成参数，兼容原工作流配置。
def build_payload(config: dict[str, Any], text: str) -> dict[str, Any]:
    aux_ref_audio_paths = config.get("aux_ref_audio_paths", [])
    payload = {
        "text": text,
        "text_lang": config["text_lang"],
        "ref_audio_path": resolve_audio_path_for_api(config["ref_audio_path"]),
        "aux_ref_audio_paths": [resolve_audio_path_for_api(path) for path in aux_ref_audio_paths],
        "prompt_text": config.get("prompt_text", ""),
        "prompt_lang": config["prompt_lang"],
    }
    payload.update(config.get("tts", {}))
    return payload


# 中文维护：命令行短句连通性检查，并非工作台暴露的 HTTP 路由。
def check_api(config: dict[str, Any]) -> int:
    api_url = config["api_url"]
    payload = build_payload(config, "接口测试。")
    output_dir = resolve_path(config.get("output_dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "_api_check.wav"

    print(f"Checking GPT-SoVITS API: {api_url}")
    post_tts(api_url, payload, out_path)
    print(f"OK: received wav ({wav_duration_ms(out_path)} ms): {out_path}")
    return 0


# 中文维护：执行旧命令行文案配音闭环；保留供既有用户和外部流程调用。
def run(config_path: Path, only_check_api: bool = False) -> int:
    config = load_config(config_path)
    if only_check_api:
        return check_api(config)

    input_path = resolve_path(config["input_text"])
    output_root = resolve_path(config.get("output_dir", "output"))

    project_folder = config.get("project_folder", "").strip()
    if project_folder:
        output_root = output_root / project_folder

    output_root.mkdir(parents=True, exist_ok=True)
    script = input_path.read_text(encoding="utf-8")

    # Build a readable generation folder name from the first/last text chars.
    clean_text = re.sub(r"[^\w\u4e00-\u9fff]+", "", script)

    if not clean_text:
        text_label = "untitled"
    elif len(clean_text) <= 6:
        text_label = clean_text
    else:
        text_label = f"{clean_text[:3]}_{clean_text[-3:]}"

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    project_name = f"{text_label}_{timestamp}"

    project_dir = output_root / project_name
    segment_dir = project_dir / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    segments = split_script(script)
    if not segments:
        raise RuntimeError(f"No text found in {input_path}.")

    max_retries = int(config.get("segment_max_retries", 2))
    retry_backoff = float(config.get("segment_retry_backoff_seconds", 0.8))
    retry_seeds = [int(seed) for seed in config.get("segment_retry_seeds", [])]
    print(f"Found {len(segments)} sentence(s).")
    for segment in segments:
        wav_path = segment_dir / f"{segment.index:03}.wav"
        segment.wav_path = wav_path
        payload = build_payload(config, segment.text)
        print(f"正在生成第 {segment.index}/{len(segments)} 句：{segment.text}", flush=True)
        generate_segment_with_retry(
            config["api_url"], payload, segment, wav_path, max_retries, retry_backoff, retry_seeds
        )
        # This is the only progress-shaped line: the GUI counts validated files only.
        print(f"[{segment.index:03}/{len(segments):03}] 已验证通过", flush=True)

    full_wav = project_dir / f"{project_name}.wav"
    srt_path = project_dir / f"{project_name}.srt"
    manifest_path = project_dir / "manifest.json"

    combine_wavs(
        segments,
        full_wav,
        int(config.get("sentence_gap_ms", 250)),
        int(config.get("paragraph_gap_ms", 550)),
    )
    write_srt(segments, srt_path)
    write_manifest(segments, manifest_path, full_wav, srt_path)

    print("\nDone.")
    print(f"Voice: {full_wav}")
    print(f"SRT:   {srt_path}")
    print(f"Parts: {segment_dir}")
    return 0

# 中文维护：解析旧命令行参数并把失败转换为非零退出码。
def main() -> int:
    parser = argparse.ArgumentParser(description="Script text to GPT-SoVITS wav segments, full wav, and SRT.")
    parser.add_argument("--config", default=str(ROOT / "config.json"), help="Path to config.json")
    parser.add_argument("--check-api", action="store_true", help="Send one short request to GPT-SoVITS API")
    args = parser.parse_args()

    try:
        return run(resolve_path(args.config, Path.cwd()), args.check_api)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
