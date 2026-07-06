#!/usr/bin/env python3
"""One-shot installer: native voice bubbles + multi-bubble replies for
Hermes Weixin AND QQ gateways.

What it does (all steps idempotent, originals backed up as *.bak-voice-bubble):

1. deps    — apt ffmpeg (+build tools), pip 'pilk' into the Hermes venv.
2. encoder — build kn007 silk_v3_encoder (supports -tencent) into
             ~/.hermes/bin/silk_v3_encoder.
3. weixin  — patch gateway/platforms/weixin.py:
             * SILK helpers (_convert_audio_to_weixin_silk, _probe_audio_duration_ms)
             * send_voice(): MP3 → Tencent SILK v3 → native bubble
               (encode_type=6, sample_rate=24000, bits=16, playtime=<ms>)
             * send_weixin_direct(): audio media → send_voice (multi-bubble
               via the send_message tool)
4. qqbot   — patch gateway/platforms/qqbot/adapter.py:
             * _convert_audio_to_qq_silk (pilk first, binary fallback)
             * send_voice(): convert local audio → SILK before upload
5. sendtool— patch tools/send_message_tool.py:
             * _send_qqbot_with_media(): text + voice/image/video/file via
               the live gateway adapter (enables N voice bubbles per turn)

After running: restart the gateway from a NORMAL shell (not from inside a
chat):   hermes gateway restart

Supported upstream variants:
  * 2026-06+ Hermes where weixin send_voice force-falls-back to file
    attachment ("not proven-working" comment).
  * Older Hermes matching the v1 skill anchors.
If an anchor is missing the script says exactly which patch failed so an AI
agent can apply the equivalent edit manually (see docs/ in this repo).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
HERMES_REPO = Path(os.environ.get("HERMES_REPO", HERMES_HOME / "hermes-agent")).expanduser()
VENV_PIP = HERMES_REPO / "venv" / "bin" / "pip"
WEIXIN = HERMES_REPO / "gateway" / "platforms" / "weixin.py"
QQBOT = HERMES_REPO / "gateway" / "platforms" / "qqbot" / "adapter.py"
SENDTOOL = HERMES_REPO / "tools" / "send_message_tool.py"
ENCODER = HERMES_HOME / "bin" / "silk_v3_encoder"
SILK_REPO = os.environ.get("SILK_V3_REPO", "https://github.com/areCodeOI/silk-v3-encoder.git")

FAILURES: list[str] = []


def log(msg: str) -> None:
    print(f"[voice-bubble] {msg}")


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"[voice-bubble] FAILED: {msg}", file=sys.stderr)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".bak-voice-bubble")
    if not bak.exists():
        shutil.copy2(path, bak)


# ---------------------------------------------------------------------------
# 1. Dependencies
# ---------------------------------------------------------------------------

def install_deps() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    build_missing = [t for t in ("git", "make", "gcc") if not shutil.which(t)]
    pkgs = []
    if missing:
        pkgs.append("ffmpeg")
    if build_missing:
        pkgs.append("build-essential")
        if "git" in build_missing:
            pkgs.append("git")
    if pkgs:
        try:
            run(["sudo", "-n", "apt-get", "install", "-y", *pkgs])
        except Exception:
            fail(f"could not apt-get install {pkgs}; install manually and rerun")

    pip = VENV_PIP if VENV_PIP.exists() else Path(sys.executable).with_name("pip")
    try:
        run([str(pip), "install", "pilk"], capture_output=True)
        log("pilk installed into Hermes venv")
    except Exception:
        fail("pip install pilk failed (QQ falls back to the binary encoder)")


# ---------------------------------------------------------------------------
# 2. SILK encoder binary
# ---------------------------------------------------------------------------

def install_encoder() -> None:
    if ENCODER.exists() and os.access(ENCODER, os.X_OK):
        log(f"encoder already installed: {ENCODER}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        checkout = Path(tmp) / "silk-v3-encoder"
        run(["git", "clone", "--depth", "1", SILK_REPO, str(checkout)])
        silk = checkout / "silk"
        run(["make", "lib"], cwd=silk, capture_output=True)
        run(["make", "encoder"], cwd=silk, capture_output=True)
        ENCODER.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(silk / "encoder", ENCODER)
        ENCODER.chmod(0o755)
    usage = subprocess.run([str(ENCODER)], capture_output=True, text=True).stdout \
        + subprocess.run([str(ENCODER)], capture_output=True, text=True).stderr
    if "-tencent" not in usage:
        fail("built encoder does not advertise -tencent flag")
    log(f"installed encoder: {ENCODER}")


# ---------------------------------------------------------------------------
# 3. weixin.py
# ---------------------------------------------------------------------------

WEIXIN_HELPERS = '''

# Native Weixin voice bubbles require Tencent-compatible SILK v3 audio plus a
# non-zero voice_item.playtime. Plain MP3/WAV/AMR payloads either render as
# file attachments or produce an unplayable "0秒" bubble.
SILK_ENCODER_PATH = get_hermes_home() / "bin" / "silk_v3_encoder"
# Real WeChat hold-to-talk voice messages report sample_rate=16000 (verified
# against a live inbound voice_item); the client player rejects other rates.
WEIXIN_VOICE_SAMPLE_RATE = 16000


def _convert_audio_to_weixin_silk(audio_path: str) -> Optional[str]:
    """Convert playable audio to Tencent-compatible SILK v3 for voice bubbles.

    Returns the .silk path on success, or None when the encoder is missing or
    conversion fails (callers fall back to a file attachment).
    """
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
    """Duration in ms for a voice payload; prefers the pre-SILK source file."""
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
        # Fall back to counting 20ms SILK frames after the header.
        try:
            data = Path(path).read_bytes()
            offset = 10 if data.startswith(b"\\x02#!SILK_V3") else 9 if data.startswith(b"#!SILK_V3") else 0
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

WEIXIN_SEND_VOICE_NEW = '''        if not self._send_session or not self._token:
            return SendResult(success=False, error="Not connected")

        # Native bot voice bubbles are currently NOT rendered by the WeChat
        # client even though iLink accepts the message (verified 2026-07-06:
        # payload and voice_item mirror a real inbound voice exactly, server
        # returns ret=0, client shows nothing; upstream reverted its own
        # native-voice attempt in Apr 2026 for the same reason). Default to
        # the reliable file-attachment fallback; set WEIXIN_NATIVE_VOICE=1
        # to re-enable the bubble path if Tencent turns rendering back on.
        native_voice = os.getenv("WEIXIN_NATIVE_VOICE", "").strip() in {"1", "true", "yes"}
        silk_path = None
        if native_voice:
            silk_path = await asyncio.to_thread(_convert_audio_to_weixin_silk, audio_path)
        try:
            if silk_path:
                message_id = await self._send_file(
                    chat_id,
                    silk_path,
                    caption or "",
                    force_file_attachment=False,
                )
            else:
                message_id = await self._send_file(
                    chat_id,
                    audio_path,
                    caption or "[voice message as attachment]",
                    force_file_attachment=True,
                )
            return SendResult(success=True, message_id=message_id)
        except Exception as exc:
            logger.error("[%s] send_voice failed to=%s: %s", self.name, _safe_id(chat_id), exc)
            return SendResult(success=False, error=str(exc))
'''


def patch_weixin() -> None:
    if not WEIXIN.exists():
        fail(f"weixin.py not found: {WEIXIN}")
        return
    text = WEIXIN.read_text()
    original = text

    # -- import subprocess
    if "\nimport subprocess\n" not in text:
        for anchor in ("import struct\n", "import secrets\n", "import re\n"):
            if anchor in text:
                text = text.replace(anchor, anchor + "import subprocess\n", 1)
                break
        else:
            fail("weixin.py: no import anchor for subprocess")

    # -- helper functions
    if "_convert_audio_to_weixin_silk" not in text:
        for anchor in ("MEDIA_VOICE = 4\n", "MESSAGE_DEDUP_TTL_SECONDS = 300\n"):
            if anchor in text:
                text = text.replace(anchor, anchor + WEIXIN_HELPERS, 1)
                break
        else:
            fail("weixin.py: no anchor for SILK helpers (MEDIA_VOICE/MESSAGE_DEDUP)")

    # -- send_voice
    if "_convert_audio_to_weixin_silk, audio_path" not in text:
        # Variant A (2026-06+): forced file-attachment fallback.
        start_marker = "        if not self._send_session or not self._token:\n            return SendResult(success=False, error=\"Not connected\")\n\n        # Native outbound Weixin voice bubbles are not proven-working"
        if start_marker in text:
            start = text.index(start_marker)
            end_marker = "            return SendResult(success=False, error=str(exc))\n"
            end = text.index(end_marker, start) + len(end_marker)
            text = text[:start] + WEIXIN_SEND_VOICE_NEW + text[end:]
        else:
            # Variant B (v1 skill era): plain _send_file(audio_path, ...).
            anchor = "        if not self._send_session or not self._token:\n            return SendResult(success=False, error=\"Not connected\")\n\n"
            old_call = "                audio_path,\n                caption or \"\",\n                force_file_attachment=False,"
            if anchor in text and old_call in text:
                text = text.replace(
                    anchor,
                    anchor + "        silk_path = await asyncio.to_thread(_convert_audio_to_weixin_silk, audio_path)\n        audio_path = silk_path or audio_path\n\n",
                    1,
                )
            else:
                fail("weixin.py: send_voice anchors not found — patch manually (see docs)")

    # -- playtime in the .silk voice_item branch
    if "_probe_audio_duration_ms(path)" not in text:
        old = '''        if media_type == MEDIA_VOICE and path.endswith(".silk"):
            item_kwargs["encode_type"] = 6
            item_kwargs["sample_rate"] = 24000
            item_kwargs["bits_per_sample"] = 16
'''
        new = '''        if media_type == MEDIA_VOICE and path.endswith(".silk"):
            # Real inbound WeChat voice reports encode_type=4 for its SILK
            # payloads (the proto-comment enum saying 4=speex is wrong for
            # iLink); mirror the client exactly.
            item_kwargs["encode_type"] = 4
            item_kwargs["sample_rate"] = WEIXIN_VOICE_SAMPLE_RATE
            item_kwargs["bits_per_sample"] = 16
            # A zero playtime yields a "0秒" bubble that will not play even
            # though WeChat STT can transcribe it.
            playtime_ms = _probe_audio_duration_ms(path)
            if playtime_ms:
                item_kwargs["playtime"] = playtime_ms
'''
        if old in text:
            text = text.replace(old, new, 1)
        else:
            fail("weixin.py: .silk voice_item block not found — patch manually")

    # -- check the iLink response for media sendmessage (text path already
    #    checks ret/errcode; without this, media rejections are silent)
    if "iLink media sendmessage error" not in text:
        old = '''        last_message_id = f"hermes-weixin-{uuid.uuid4().hex}"
        await _api_post(
            self._send_session,
            base_url=self._base_url,
            endpoint=EP_SEND_MESSAGE,
'''
        new = '''        last_message_id = f"hermes-weixin-{uuid.uuid4().hex}"
        media_resp = await _api_post(
            self._send_session,
            base_url=self._base_url,
            endpoint=EP_SEND_MESSAGE,
'''
        if old in text:
            text = text.replace(old, new, 1)
            old_ret = '''            token=self._token,
            timeout_ms=API_TIMEOUT_MS,
        )
        return last_message_id

    def _outbound_media_builder(self'''
            new_ret = '''            token=self._token,
            timeout_ms=API_TIMEOUT_MS,
        )
        # The text path checks ret/errcode; media must too, otherwise a
        # server-side rejection (rate limit, invalid item) is silently
        # swallowed and the file never reaches the user.
        if isinstance(media_resp, dict):
            ret = media_resp.get("ret")
            errcode = media_resp.get("errcode")
            if (ret not in (None, 0)) or (errcode not in (None, 0)):
                raise RuntimeError(
                    f"iLink media sendmessage error: ret={ret} errcode={errcode} "
                    f"errmsg={media_resp.get('errmsg') or media_resp.get('msg')} "
                    f"media_type={media_type}"
                )
        logger.info(
            "[%s] media sendmessage accepted: type=%s file=%s",
            self.name, media_type, Path(path).name,
        )
        return last_message_id

    def _outbound_media_builder(self'''
            if old_ret in text:
                text = text.replace(old_ret, new_ret, 1)
            else:
                fail("weixin.py: media send return anchor not found")
        else:
            fail("weixin.py: media _api_post anchor not found")

    # -- voice_item builder: mirror a real inbound voice item (no
    #    encrypt_type — that is image semantics — plus completion metadata)
    if '"is_completed": True' not in text:
        old = '''            return MEDIA_VOICE, lambda **kw: {
                "type": ITEM_VOICE,
                "voice_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
'''
        new = '''            return MEDIA_VOICE, lambda **kw: {
                "type": ITEM_VOICE,
                # Real inbound voice items carry completion metadata; without
                # it the client may treat the item as still streaming.
                "is_completed": True,
                "create_time_ms": int(time.time() * 1000),
                "update_time_ms": int(time.time() * 1000),
                "voice_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                    },
'''
        if old in text:
            text = text.replace(old, new, 1)
        else:
            fail("weixin.py: voice_item builder block not found")

    # -- upload via upload_param-constructed CDN URL (the path proven by
    #    working implementations); upload_full_url only as fallback
    old = '''        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = _cdn_upload_url(self._cdn_base_url, upload_param, filekey)
'''
    new = '''        if upload_param:
            upload_url = _cdn_upload_url(self._cdn_base_url, upload_param, filekey)
        elif upload_full_url:
            upload_url = upload_full_url
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        fail("weixin.py: upload URL priority block not found")

    # -- send_weixin_direct: route audio through send_voice (both loops)
    audio_branch = '''            elif is_voice or ext in {".mp3", ".wav", ".ogg", ".opus", ".m4a", ".flac", ".silk"}:
'''
    if audio_branch not in text:
        for adapter_var in ("live_adapter", "adapter"):
            old = f'''        for media_path, _is_voice in media_files or []:
            ext = Path(media_path).suffix.lower()
            if ext in {{".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}}:
                last_result = await {adapter_var}.send_image_file(chat_id, media_path)
            else:
                last_result = await {adapter_var}.send_document(chat_id, media_path)
'''
            new = f'''        for media_path, is_voice in media_files or []:
            ext = Path(media_path).suffix.lower()
            if ext in {{".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}}:
                last_result = await {adapter_var}.send_image_file(chat_id, media_path)
            elif is_voice or ext in {{".mp3", ".wav", ".ogg", ".opus", ".m4a", ".flac", ".silk"}}:
                last_result = await {adapter_var}.send_voice(chat_id, media_path)
            else:
                last_result = await {adapter_var}.send_document(chat_id, media_path)
'''
            if old in text:
                text = text.replace(old, new, 1)
            else:
                fail(f"weixin.py: send_weixin_direct {adapter_var} media loop not found")

    if text != original:
        backup(WEIXIN)
        WEIXIN.write_text(text)
        log("patched weixin.py")
    else:
        log("weixin.py already patched")


# ---------------------------------------------------------------------------
# 4. qqbot/adapter.py
# ---------------------------------------------------------------------------

QQ_HELPER = '''def _convert_audio_to_qq_silk(audio_path: str) -> Optional[str]:
    """Convert audio to Tencent SILK v3 so QQ renders a native voice bubble.

    QQ's rich-media voice endpoint (file_type=3) only plays SILK payloads;
    other formats arrive as plain file attachments. Tries the pure-Python
    ``pilk`` encoder first, then the standalone ``silk_v3_encoder`` binary.
    Returns the .silk path, or None so callers can fall back to sending the
    original file.
    """
    import subprocess
    import tempfile

    source = Path(audio_path)
    if source.suffix.lower() == ".silk":
        return str(source)
    if not source.exists():
        return None

    sample_rate = 24000
    output_path = source.with_suffix(source.suffix + ".silk")
    pcm_path = Path(tempfile.gettempdir()) / f"{output_path.name}.pcm"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), str(pcm_path),
        ], check=True, capture_output=True, timeout=30)

        try:
            import pilk
            pilk.encode(str(pcm_path), str(output_path), pcm_rate=sample_rate, tencent=True)
            return str(output_path)
        except Exception as exc:
            logger.debug("[QQBot] pilk SILK encode failed, trying binary encoder: %s", exc)

        encoder = Path.home() / ".hermes" / "bin" / "silk_v3_encoder"
        if encoder.exists():
            subprocess.run([
                str(encoder), str(pcm_path), str(output_path),
                "-Fs_API", str(sample_rate), "-Fs_maxInternal", str(sample_rate),
                "-packetlength", "20", "-rate", "25000", "-tencent", "-quiet",
            ], check=True, capture_output=True, timeout=30)
            return str(output_path)
        logger.warning("[QQBot] no SILK encoder available (pilk failed, %s missing)", encoder)
        return None
    except Exception as exc:
        logger.warning("[QQBot] failed to convert %s to SILK: %s", audio_path, exc)
        return None
    finally:
        try:
            pcm_path.unlink(missing_ok=True)
        except Exception:
            pass


'''

QQ_SEND_VOICE_OLD = '''        """Send a voice message natively."""
        del kwargs
        return await self._send_media(
            chat_id, audio_path, MEDIA_TYPE_VOICE, "voice", caption, reply_to
        )
'''

QQ_SEND_VOICE_NEW = '''        """Send a voice message natively."""
        del kwargs
        # QQ voice bubbles require SILK; a non-SILK upload with file_type=3
        # degrades to a file attachment on the receiving client.
        send_path = audio_path
        if not self._is_url(audio_path):
            silk_path = await asyncio.to_thread(_convert_audio_to_qq_silk, audio_path)
            if silk_path:
                send_path = silk_path
        return await self._send_media(
            chat_id, send_path, MEDIA_TYPE_VOICE, "voice", caption, reply_to
        )
'''


def patch_qqbot() -> None:
    if not QQBOT.exists():
        log("qqbot adapter not found — skipping (QQ not installed in this Hermes)")
        return
    text = QQBOT.read_text()
    original = text

    if "_convert_audio_to_qq_silk" not in text:
        anchor = "class QQCloseError(Exception):"
        if anchor in text:
            text = text.replace(anchor, QQ_HELPER + anchor, 1)
        else:
            fail("qqbot/adapter.py: QQCloseError anchor not found")

    if "silk_path = await asyncio.to_thread(_convert_audio_to_qq_silk" not in text:
        if QQ_SEND_VOICE_OLD in text:
            text = text.replace(QQ_SEND_VOICE_OLD, QQ_SEND_VOICE_NEW, 1)
        else:
            fail("qqbot/adapter.py: send_voice body not found — patch manually")

    # -- older-upstream bug: rich-media message body used a per-kind key
    #    ("voice"/"image"/...) but QQ API v2 only accepts "media"; voice
    #    sends then fail with HTTP 500 "invalid file_info" and degrade to
    #    file attachments. Current upstream already hardcodes "media".
    old_media_key = '''            media_key = kind if kind in ("voice", "image", "video", "file") else "media"\n'''
    if old_media_key in text:
        text = text.replace(
            old_media_key,
            '''            # QQ API v2 expects the rich-media payload under "media" for every
            # file_type; per-kind keys ("voice"/...) are rejected with
            # "invalid file_info" (HTTP 500). Matches current upstream.
            media_key = "media"\n''',
            1,
        )

    # -- upstream bug: gateway/run.py calls adapter.connect(is_reconnect=...)
    #    but QQAdapter.connect() does not accept it, so QQ never connects.
    if "async def connect(self, is_reconnect" not in text:
        old = '''    async def connect(self) -> bool:
        """Authenticate, obtain gateway URL, and open the WebSocket."""
'''
        new = '''    async def connect(self, is_reconnect: bool = False) -> bool:
        """Authenticate, obtain gateway URL, and open the WebSocket.

        ``is_reconnect`` is forwarded by the gateway runner for adapters that
        distinguish cold boots from watcher reconnects; QQ handles both the
        same way, the parameter just keeps the call signature compatible.
        """
        del is_reconnect
'''
        if old in text:
            text = text.replace(old, new, 1)
        else:
            fail("qqbot/adapter.py: connect() signature anchor not found")

    if text != original:
        backup(QQBOT)
        QQBOT.write_text(text)
        log("patched qqbot/adapter.py")
    else:
        log("qqbot/adapter.py already patched")


# ---------------------------------------------------------------------------
# 5. tools/send_message_tool.py
# ---------------------------------------------------------------------------

SENDTOOL_QQ_BRANCH = '''    # --- QQBot: media delivery needs the live gateway adapter (its
    # send_voice converts audio to SILK so QQ renders native voice bubbles;
    # the standalone REST path below is text-only).
    if platform == Platform.QQBOT and media_files:
        qq_result = await _send_qqbot_with_media(chat_id, chunks, media_files)
        if qq_result is not None:
            return qq_result
        # No live adapter — fall through; text still goes out below with a
        # warning that media was omitted.

'''

SENDTOOL_QQ_HELPER = '''async def _send_qqbot_with_media(chat_id, chunks, media_files):
    """Send text + media via the live QQBot gateway adapter.

    Returns a result dict, or None when no live adapter is available so the
    caller can fall back to the text-only standalone REST path.
    """
    try:
        from gateway.run import _gateway_runner_ref
        from gateway.config import Platform as _Platform
        runner = _gateway_runner_ref()
        adapter = runner.adapters.get(_Platform.QQBOT) if runner else None
    except Exception:
        adapter = None
    if adapter is None:
        return None

    last_result = None
    for chunk in chunks:
        if not chunk.strip():
            continue
        last_result = await adapter.send(chat_id, chunk)
        if not last_result.success:
            return _error(f"QQBot send failed: {last_result.error}")

    for media_path, is_voice in media_files:
        if not os.path.exists(media_path):
            return _error(f"Media file not found: {media_path}")
        ext = os.path.splitext(media_path)[1].lower()
        if ext in _IMAGE_EXTS:
            last_result = await adapter.send_image_file(chat_id, media_path)
        elif ext in _VIDEO_EXTS:
            last_result = await adapter.send_video(chat_id, media_path)
        elif is_voice or ext in _AUDIO_EXTS:
            last_result = await adapter.send_voice(chat_id, media_path)
        else:
            last_result = await adapter.send_document(chat_id, media_path)
        if not last_result.success:
            return _error(f"QQBot media send failed: {last_result.error}")

    if last_result is None:
        return _error("No deliverable text or media remained after processing MEDIA tags")
    return {
        "success": True,
        "platform": "qqbot",
        "chat_id": chat_id,
        "message_id": last_result.message_id,
    }


'''


def patch_sendtool() -> None:
    if not SENDTOOL.exists():
        fail(f"send_message_tool.py not found: {SENDTOOL}")
        return
    text = SENDTOOL.read_text()
    original = text

    if "_send_qqbot_with_media" not in text:
        anchor = "    # --- Non-media platforms ---\n"
        if anchor in text:
            text = text.replace(anchor, SENDTOOL_QQ_BRANCH + anchor, 1)
        else:
            fail("send_message_tool.py: non-media anchor not found")

        helper_anchor = "async def _send_qqbot(pconfig, chat_id, message):"
        if helper_anchor in text:
            text = text.replace(helper_anchor, SENDTOOL_QQ_HELPER + helper_anchor, 1)
        else:
            fail("send_message_tool.py: _send_qqbot anchor not found")

        # Cosmetic: mention qqbot in the "unsupported media" messages.
        text = text.replace(
            "telegram, discord, matrix, weixin, signal, yuanbao, feishu and whatsapp",
            "telegram, discord, matrix, weixin, signal, yuanbao, feishu, whatsapp and qqbot (gateway running)",
        )

    if text != original:
        backup(SENDTOOL)
        SENDTOOL.write_text(text)
        log("patched send_message_tool.py")
    else:
        log("send_message_tool.py already patched")


# ---------------------------------------------------------------------------
# 6. verify
# ---------------------------------------------------------------------------

def verify() -> None:
    py = HERMES_REPO / "venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    for f in (WEIXIN, QQBOT, SENDTOOL):
        if f.exists():
            run([str(py), "-m", "py_compile", str(f)], cwd=HERMES_REPO)
    log("compile check OK")

    if shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "t.mp3"
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                 "sine=frequency=440:duration=2", "-ac", "1", str(mp3)],
                capture_output=True)
            code = (
                "import sys; sys.path.insert(0, %r);"
                "from gateway.platforms.weixin import _convert_audio_to_weixin_silk as c, _probe_audio_duration_ms as p;"
                "s = c(%r); assert s, 'conversion failed';"
                "d = open(s,'rb').read(); assert d[:12].find(b'#!SILK_V3') in (0,1), d[:12];"
                "ms = p(s); assert ms and ms > 1000, ms;"
                "print('[voice-bubble] weixin SILK pipeline OK, duration', ms, 'ms')"
            ) % (str(HERMES_REPO), str(mp3))
            run([str(py), "-c", code], cwd=HERMES_REPO)


def main() -> None:
    log(f"HERMES_HOME={HERMES_HOME}  HERMES_REPO={HERMES_REPO}")
    install_deps()
    install_encoder()
    patch_weixin()
    patch_qqbot()
    patch_sendtool()
    verify()
    if FAILURES:
        print("\n[voice-bubble] finished WITH FAILURES:")
        for f in FAILURES:
            print("  -", f)
        print("Apply the failed patches manually — see docs/VOICE_BUBBLE_SKILL_V2.md")
        sys.exit(1)
    log("all patches applied. Now restart the gateway from a normal shell:")
    log("    hermes gateway restart")


if __name__ == "__main__":
    main()
