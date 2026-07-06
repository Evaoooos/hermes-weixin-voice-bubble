---
name: hermes-voice-bubble-setup-v2
description: Use when a Hermes Agent Weixin/WeChat or QQ gateway should reply with native voice bubbles (not MP3 file attachments), including multiple voice bubbles in a single turn like a real person chatting. Installs Tencent-compatible SILK v3 encoding and patches the Weixin adapter, QQ adapter, and send_message tool.
version: 2.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, weixin, wechat, qq, qqbot, voice, tts, silk, multi-bubble]
    supersedes: weixin-voice-bubble-setup
---

# Hermes Voice Bubble Setup v2 (Weixin + QQ, multi-bubble)

## Overview

Both WeChat (iLink) and the official QQ Bot API only render **native voice
bubbles** for **Tencent-compatible SILK v3** audio. Hermes TTS produces MP3,
so without conversion:

- **Weixin**: audio arrives as a file attachment (newer Hermes builds even
  hard-code `force_file_attachment=True` in `send_voice` with a comment that
  bubbles are "not proven-working" — they DO work with SILK + `playtime`).
- **QQ**: an MP3 uploaded as `file_type=3` (voice) degrades to a plain file
  on the receiving client; only SILK plays as a bubble.
- **Multi-bubble replies**: mid-turn messages go through
  `send_weixin_direct()` / `_send_qqbot()` (the `send_message` tool), which
  historically sent ALL audio as documents (weixin) or dropped media
  entirely (qqbot). That is why "first N messages are MP3 files, only the
  last is a bubble".

## One-Shot Setup

```bash
python3 scripts/install_voice_bubble_v2.py
hermes gateway restart        # from a normal shell, never from inside a chat
```

Environment overrides: `HERMES_HOME` (default `~/.hermes`), `HERMES_REPO`
(default `$HERMES_HOME/hermes-agent`).

The script is idempotent, backs up every file it touches as
`*.bak-voice-bubble`, self-verifies (py_compile + a real MP3→SILK
conversion), and prints exactly which patch failed if the upstream code
drifted again.

## What Gets Patched (for manual/AI application when anchors drift)

### 1. `gateway/platforms/weixin.py`

- `import subprocess` at the top.
- Module-level helpers after the `MEDIA_VOICE = 4` constants:
  - `SILK_ENCODER_PATH = get_hermes_home() / "bin" / "silk_v3_encoder"`
  - `WEIXIN_VOICE_SAMPLE_RATE = 24000`
  - `_convert_audio_to_weixin_silk(path)`:
    `ffmpeg -y -v error -i in -f s16le -ac 1 -ar 24000 out.pcm`, then
    `silk_v3_encoder out.pcm out.silk -Fs_API 24000 -Fs_maxInternal 24000
    -packetlength 20 -rate 25000 -tencent -quiet`. Returns `.silk` path or
    None.
  - `_probe_audio_duration_ms(path)`: ffprobe on the pre-SILK source, else
    count 20 ms SILK frames after the `#!SILK_V3` header.
- `send_voice()`: convert to SILK; on success call
  `self._send_file(chat_id, silk_path, caption or "", force_file_attachment=False)`;
  on failure keep the old file-attachment fallback. Run conversion via
  `asyncio.to_thread` (it shells out to ffmpeg).
- `_send_file()` `.silk` branch must set **all** of:
  `encode_type=6`, `sample_rate=24000`, `bits_per_sample=16`, and
  `playtime=<duration ms>`. **Zero playtime = "0秒" bubble that WeChat STT
  can transcribe but cannot play.**
- `_outbound_media_builder()` `.silk` branch must forward
  `playtime: kw.get("playtime", 0)` in `voice_item` (upstream already does).
- `send_weixin_direct()` (used by the `send_message` tool → multi-bubble):
  in BOTH media loops (live adapter + ephemeral adapter), route
  `is_voice or ext in {mp3,wav,ogg,opus,m4a,flac,silk}` → `send_voice(...)`
  instead of `send_document(...)`.

### 2. `gateway/platforms/qqbot/adapter.py`

- Module-level `_convert_audio_to_qq_silk(path)`: same ffmpeg→PCM step, then
  `pilk.encode(pcm, silk, pcm_rate=24000, tencent=True)` with the
  `silk_v3_encoder` binary as fallback.
- `send_voice()`: for local (non-URL) audio, convert to SILK first, then
  `self._send_media(chat_id, silk_path, MEDIA_TYPE_VOICE, "voice", ...)`.

### 3. `tools/send_message_tool.py`

- New `_send_qqbot_with_media(chat_id, chunks, media_files)`: fetch the live
  adapter via `gateway.run._gateway_runner_ref()`; send text chunks with
  `adapter.send`, then dispatch each media file by extension
  (image → `send_image_file`, video → `send_video`,
  audio/is_voice → `send_voice`, else `send_document`).
- In `_send_to_platform()`, before the "Non-media platforms" block:
  `if platform == Platform.QQBOT and media_files:` call the helper; fall
  through to the text-only REST path only when no live adapter exists.

## Dependencies

- `ffmpeg` + `ffprobe` (apt).
- `pilk` (pip, into the Hermes venv) — pure-pip SILK codec used by QQ.
- `silk_v3_encoder` built from https://github.com/areCodeOI/silk-v3-encoder
  (`make lib && make encoder` in `silk/`), installed to `~/.hermes/bin/`.
  Usage text must mention `-tencent`.

## Getting Multiple Voice Bubbles Per Reply

The delivery plumbing above makes every audio file sent through
`adapter.send_voice` a native bubble. To make the agent actually send
several short bubbles instead of one long one, instruct it (SOUL.md /
personality / a direct chat message):

> 语音回复时：把回复拆成多条简短的口语化句子，对每一条分别调用
> `text_to_speech` 生成音频；把所有生成的 `MEDIA:<path>` 标签都放进最终回复
> （或者用 `send_message` 工具把每条音频作为独立消息发出）。不要把整段话
> 合成成一个音频。

Every `MEDIA:<audio>` tag in the final response is dispatched sequentially
through `send_voice` → one bubble each. `send_message`-tool sends with
audio MEDIA tags now also produce bubbles on both platforms.

## Verification Checklist

- [ ] `~/.hermes/bin/silk_v3_encoder` exists; usage mentions `-tencent`.
- [ ] `ffmpeg`/`ffprobe` on PATH; `venv/bin/python -c "import pilk"` works.
- [ ] Installer verify step prints `weixin SILK pipeline OK`.
- [ ] Gateway restarted from OUTSIDE any chat.
- [ ] Weixin: TTS reply arrives as a bubble with a real duration (not 0秒),
      plays audio, and long-press → 转文字 still works.
- [ ] Weixin: asking for 3 short voice messages produces 3 bubbles.
- [ ] QQ: voice arrives as a playable bubble; multi-voice turns produce
      multiple bubbles, none as MP3 files.

## Known Failure Modes

- **"0秒" bubble**: `playtime` missing → check `_probe_audio_duration_ms`.
- **File attachment instead of bubble (weixin)**: send_voice still has
  `force_file_attachment=True`, or the SILK encoder is missing so the
  fallback fired — check gateway logs for `[Weixin] SILK encoder missing`.
- **MP3 file on QQ**: pilk not installed AND binary encoder missing.
- **Worked before, broke after other changes on the VM**: a Hermes
  update/reinstall overwrote the patched files (patches live inside the
  hermes-agent checkout). Re-run the installer — it is idempotent.
- **Never restart the gateway from inside a gateway chat** — Hermes blocks
  it to prevent restart loops.
