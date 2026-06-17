#!/usr/bin/env python3
"""Install Weixin voice-bubble SILK support for a Hermes checkout.

This script is intentionally conservative: it installs a Tencent-compatible
SILK v3 encoder and patches the Weixin adapter only when the expected anchor
points are present.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
HERMES_REPO = Path(os.environ.get("HERMES_REPO", HERMES_HOME / "hermes-agent")).expanduser()
WEIXIN = HERMES_REPO / "gateway" / "platforms" / "weixin.py"
ENCODER = HERMES_HOME / "bin" / "silk_v3_encoder"
VENDOR = HERMES_HOME / "vendor" / "silk-v3"
REPO_URL = os.environ.get("SILK_V3_REPO", "https://github.com/areCodeOI/silk-v3-encoder.git")

HELPERS = r'''
SILK_ENCODER_PATH = get_hermes_home() / "bin" / "silk_v3_encoder"
WEIXIN_VOICE_SAMPLE_RATE = 24000


def _convert_audio_to_weixin_silk(audio_path: str) -> Optional[str]:
    """Convert playable audio to Tencent-compatible SILK v3 for Weixin voice bubbles."""
    source = Path(audio_path)
    if source.suffix.lower() == ".silk":
        return str(source)
    if not SILK_ENCODER_PATH.exists():
        logger.warning("[Weixin] SILK encoder missing at %s", SILK_ENCODER_PATH)
        return None
    if not source.exists():
        return None

    output_path = source.with_suffix(source.suffix + ".silk")
    pcm_path = output_path.with_suffix(output_path.suffix + ".pcm")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-f", "s16le", "-ac", "1", "-ar", str(WEIXIN_VOICE_SAMPLE_RATE), str(pcm_path),
        ], check=True, capture_output=True, timeout=30)
        subprocess.run([
            str(SILK_ENCODER_PATH), str(pcm_path), str(output_path),
            "-Fs_API", str(WEIXIN_VOICE_SAMPLE_RATE),
            "-Fs_maxInternal", str(WEIXIN_VOICE_SAMPLE_RATE),
            "-packetlength", "20", "-rate", "25000", "-tencent", "-quiet",
        ], check=True, capture_output=True, timeout=30)
        return str(output_path)
    except Exception as exc:
        logger.warning("[Weixin] failed to convert %s to SILK: %s", audio_path, exc, exc_info=True)
        return None
    finally:
        try:
            pcm_path.unlink(missing_ok=True)
        except Exception:
            pass


def _probe_audio_duration_ms(path: str) -> Optional[int]:
    candidates = [Path(path)]
    if path.endswith(".silk"):
        silk_path = Path(path)
        if silk_path.name.endswith(".mp3.silk") or silk_path.name.endswith(".wav.silk"):
            candidates.insert(0, Path(str(silk_path)[:-5]))

    for candidate in candidates:
        if not candidate.exists() or candidate.suffix.lower() == ".silk":
            continue
        try:
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(candidate),
            ], capture_output=True, text=True, timeout=5)
            duration_sec = float(probe.stdout.strip())
            if duration_sec > 0:
                return max(1, int(duration_sec * 1000))
        except Exception:
            continue

    if path.endswith(".silk"):
        try:
            data = Path(path).read_bytes()
            offset = 10 if data.startswith(b"\x02#!SILK_V3") else 9 if data.startswith(b"#!SILK_V3") else 0
            frame_count = 0
            while offset + 2 <= len(data):
                frame_size = int.from_bytes(data[offset:offset + 2], "little")
                offset += 2
                if frame_size <= 0 or offset + frame_size > len(data):
                    break
                frame_count += 1
                offset += frame_size
            if frame_count:
                return frame_count * 20
        except Exception:
            pass
    return None
'''


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def install_encoder() -> None:
    if ENCODER.exists() and os.access(ENCODER, os.X_OK):
        print(f"encoder already installed: {ENCODER}")
        return
    if not shutil.which("git") or not shutil.which("make") or not shutil.which("gcc"):
        raise SystemExit("missing git/make/gcc; install build-essential first")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("missing ffmpeg/ffprobe; install ffmpeg first")

    with tempfile.TemporaryDirectory() as tmp:
        checkout = Path(tmp) / "silk-v3-encoder"
        run(["git", "clone", "--depth", "1", REPO_URL, str(checkout)])
        silk = checkout / "silk"
        run(["make", "lib"], cwd=silk)
        run(["make", "encoder"], cwd=silk)
        ENCODER.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(silk / "encoder", ENCODER)
        ENCODER.chmod(0o755)
        if VENDOR.exists():
            shutil.rmtree(VENDOR)
        VENDOR.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(silk, VENDOR, ignore=shutil.ignore_patterns("*.o", "*.a", "encoder", "decoder"))
    print(f"installed encoder: {ENCODER}")


def patch_weixin() -> None:
    if not WEIXIN.exists():
        raise SystemExit(f"weixin.py not found: {WEIXIN}")
    text = WEIXIN.read_text()
    original = text

    if "SILK_ENCODER_PATH = get_hermes_home()" not in text:
        anchor = "MESSAGE_DEDUP_TTL_SECONDS = 300\n"
        if anchor not in text:
            raise SystemExit("cannot find MESSAGE_DEDUP_TTL_SECONDS anchor")
        text = text.replace(anchor, anchor + HELPERS + "\n", 1)

    if "silk_path = _convert_audio_to_weixin_silk(audio_path) or audio_path" not in text:
        anchor = "        if not self._send_session or not self._token:\n            return SendResult(success=False, error=\"Not connected\")\n\n"
        if anchor not in text:
            raise SystemExit("cannot find send_voice connection anchor")
        text = text.replace(anchor, anchor + "        silk_path = _convert_audio_to_weixin_silk(audio_path) or audio_path\n\n", 1)
        text = text.replace("                audio_path,\n                caption or \"\",\n                force_file_attachment=False,", "                silk_path,\n                caption or \"\",\n                force_file_attachment=False,", 1)

    old_silk = '''        if media_type == MEDIA_VOICE and path.endswith(".silk"):
            item_kwargs["encode_type"] = 6
            item_kwargs["sample_rate"] = 24000
            item_kwargs["bits_per_sample"] = 16
'''
    new_silk = '''        if media_type == MEDIA_VOICE and path.endswith(".silk"):
            item_kwargs["encode_type"] = 6
            item_kwargs["sample_rate"] = WEIXIN_VOICE_SAMPLE_RATE
            item_kwargs["bits_per_sample"] = 16
            playtime_ms = _probe_audio_duration_ms(path)
            if playtime_ms:
                item_kwargs["playtime"] = playtime_ms
'''
    if old_silk in text:
        text = text.replace(old_silk, new_silk, 1)
    elif "item_kwargs[\"sample_rate\"] = WEIXIN_VOICE_SAMPLE_RATE" not in text:
        raise SystemExit("cannot find silk voice_item block")

    if text != original:
        backup = WEIXIN.with_suffix(".py.bak-weixin-voice")
        shutil.copy2(WEIXIN, backup)
        WEIXIN.write_text(text)
        print(f"patched {WEIXIN}; backup at {backup}")
    else:
        print("weixin.py already patched")


def main() -> None:
    install_encoder()
    patch_weixin()
    print("done. Restart gateway from a normal shell: hermes gateway restart")


if __name__ == "__main__":
    main()
