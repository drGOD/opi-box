#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

TIMELAPSE_TS_RE = re.compile(r"(\d{8}_\d{6})")
DEFAULT_FPS = 24
DEFAULT_SIZE = (960, 720)


class ProgressBar:
    def __init__(self, width: int = 32):
        self.width = width
        self._last_len = 0

    def update(self, fraction: float, status: str) -> None:
        fraction = max(0.0, min(1.0, fraction))
        filled = round(self.width * fraction)
        bar = "#" * filled + "-" * (self.width - filled)
        text = f"\r[{bar}] {fraction * 100:6.2f}%  {status}"
        padding = " " * max(0, self._last_len - len(text))
        print(text + padding, end="", flush=True)
        self._last_len = len(text)

    def finish(self, status: str) -> None:
        self.update(1.0, status)
        print()


def timelapse_timestamp(path: Path) -> datetime | None:
    match = TIMELAPSE_TS_RE.search(path.name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")


def parse_range(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y%m%d_%H%M%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid date '{value}'. Use YYYY-MM-DDTHH:MM[:SS] or YYYYMMDD_HHMMSS."
    )


def collect_frames(input_dir: Path, start: datetime | None, end: datetime | None) -> list[tuple[Path, datetime]]:
    files = []
    for filepath in sorted(input_dir.glob("*.jpg")):
        ts = timelapse_timestamp(filepath)
        if ts is None:
            continue
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        files.append((filepath, ts))
    return files


def link_frame_sequence(files: list[tuple[Path, datetime]], temp_dir: Path, progress: ProgressBar) -> None:
    total = len(files)
    for index, (filepath, _ts) in enumerate(files):
        link_path = temp_dir / f"frame_{index:06d}.jpg"
        try:
            os.symlink(filepath, link_path)
        except OSError:
            try:
                os.link(filepath, link_path)
            except OSError:
                shutil.copyfile(filepath, link_path)
        if index % 10 == 0 or index == total - 1:
            progress.update((index + 1) / max(total, 1) * 0.25, f"Preparing frames {index + 1}/{total}")


def write_label_sequence(files: list[tuple[Path, datetime]], temp_dir: Path, progress: ProgressBar) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Pillow is required to render timestamp labels") from exc

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    total = len(files)
    padding_x = 10
    padding_y = 6
    for index, (_filepath, ts) in enumerate(files):
        label = ts.strftime("%d.%m.%Y %H:%M:%S")
        scratch = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        draw = ImageDraw.Draw(scratch)
        bbox = draw.textbbox((0, 0), label, font=font)
        label_w = bbox[2] - bbox[0]
        label_h = bbox[3] - bbox[1]
        image = Image.new("RGBA", (label_w + padding_x * 2, label_h + padding_y * 2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=6, fill=(0, 0, 0, 150))
        draw.text((padding_x, padding_y), label, font=font, fill=(255, 255, 255, 240))
        image.save(temp_dir / f"label_{index:06d}.png")
        if index % 10 == 0 or index == total - 1:
            progress.update(0.25 + (index + 1) / max(total, 1) * 0.25, f"Rendering labels {index + 1}/{total}")


def run_ffmpeg(
    *,
    ffmpeg: str,
    temp_dir: Path,
    output_path: Path,
    fps: int,
    size: tuple[int, int],
    frame_count: int,
    progress: ProgressBar,
) -> None:
    width, height = size
    filter_complex = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bilinear,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[base];"
        "[base][1:v]overlay=14:14:format=auto,"
        "split[s0][s1];"
        "[s0]palettegen=stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-framerate",
        str(fps),
        "-i",
        str(temp_dir / "frame_%06d.jpg"),
        "-framerate",
        str(fps),
        "-i",
        str(temp_dir / "label_%06d.png"),
        "-filter_complex",
        filter_complex,
        "-progress",
        "pipe:1",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    last_update = time.monotonic()
    assert proc.stdout is not None
    for line in proc.stdout:
        key, _, value = line.strip().partition("=")
        if key == "frame" and value.isdigit():
            encoded = min(int(value), frame_count)
            progress.update(0.5 + encoded / max(frame_count, 1) * 0.5, f"Encoding GIF {encoded}/{frame_count}")
            last_update = time.monotonic()
        elif key == "progress" and value == "end":
            progress.update(1.0, "Finalizing GIF")

        if time.monotonic() - last_update > 1.0:
            progress.update(0.75, "Encoding GIF")
            last_update = time.monotonic()

    stderr = proc.stderr.read() if proc.stderr is not None else ""
    if proc.wait() != 0:
        raise RuntimeError(stderr.strip() or "ffmpeg failed")


def build_gif(args: argparse.Namespace) -> Path:
    input_dir = args.input.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install ffmpeg or pass --ffmpeg /path/to/ffmpeg.")
    if not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")
    if args.start and args.end and args.start > args.end:
        raise RuntimeError("--start must be before --end")

    progress = ProgressBar()
    progress.update(0.0, "Scanning snapshots")
    files = collect_frames(input_dir, args.start, args.end)
    if not files:
        raise RuntimeError("No snapshots found for the selected range")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="growbox_gif_", dir=input_dir) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        link_frame_sequence(files, temp_dir, progress)
        write_label_sequence(files, temp_dir, progress)
        try:
            run_ffmpeg(
                ffmpeg=ffmpeg,
                temp_dir=temp_dir,
                output_path=output_path,
                fps=args.fps,
                size=(args.width, args.height),
                frame_count=len(files),
                progress=progress,
            )
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
    progress.finish(f"Done: {output_path} ({len(files)} frames, {args.fps} fps)")
    return output_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a timestamped GrowBox timelapse GIF.")
    parser.add_argument("-i", "--input", type=Path, default=Path("timelapse"), help="Directory with frame_*.jpg snapshots")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output GIF path")
    parser.add_argument("--start", type=parse_range, help="Start datetime: YYYY-MM-DDTHH:MM[:SS] or YYYYMMDD_HHMMSS")
    parser.add_argument("--end", type=parse_range, help="End datetime: YYYY-MM-DDTHH:MM[:SS] or YYYYMMDD_HHMMSS")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="Output frames per second")
    parser.add_argument("--width", type=int, default=DEFAULT_SIZE[0], help="Output width")
    parser.add_argument("--height", type=int, default=DEFAULT_SIZE[1], help="Output height")
    parser.add_argument("--ffmpeg", help="Path to ffmpeg binary")
    args = parser.parse_args(argv)
    if args.fps < 1 or args.fps > 60:
        parser.error("--fps must be between 1 and 60")
    if args.width < 64 or args.height < 64:
        parser.error("--width and --height must be at least 64")
    if args.output is None:
        args.output = Path(f"growbox_timelapse_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gif")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        build_gif(parse_args(argv or sys.argv[1:]))
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
