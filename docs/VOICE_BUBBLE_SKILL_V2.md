---
name: hermes-voice-bubble-setup-v2
description: Use when a Hermes Agent QQ gateway should reply with native voice bubbles (not MP3 file attachments), including multiple voice bubbles in a single turn like a real person chatting. Also patches the Weixin gateway (playable MP3-file fallback now; full bubble plumbing behind WEIXIN_NATIVE_VOICE=1 because current WeChat clients do not render bot voice items). Installs Tencent SILK v3 encoding and patches the QQ adapter, Weixin adapter, and send_message tool.
version: 2.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, weixin, wechat, qq, qqbot, voice, tts, silk, multi-bubble]
    supersedes: weixin-voice-bubble-setup
---

# Hermes Voice Bubble Setup v2 (QQ verified; Weixin plumbing + fallback)

## Status (verified live 2026-07-06)

| Platform | Result |
|----------|--------|
| **QQ** | ✅ Native voice bubbles, multiple per turn. Requires the SILK conversion + `connect()` fix below. |
| **Weixin** | ⚠️ Bot voice bubbles are currently **not rendered by the WeChat client**, even with a byte-exact replica of a real voice message (details below). Default = playable MP3 file attachment. Set `WEIXIN_NATIVE_VOICE=1` in `~/.hermes/.env` to re-enable the bubble path if Tencent turns rendering on. |

## One-Shot Setup

```bash
python3 scripts/install_voice_bubble_v2.py
hermes gateway restart        # from a normal shell, never from inside a chat
```

Environment overrides: `HERMES_HOME` (default `~/.hermes`), `HERMES_REPO`
(default `$HERMES_HOME/hermes-agent`).

Idempotent; originals backed up as `*.bak-voice-bubble`; self-verifies
(py_compile + real MP3→SILK conversion); names the exact patch that failed
if upstream anchors drifted.

## What Gets Patched

### 1. `gateway/platforms/qqbot/adapter.py` (the part that makes QQ work)

- **`connect(self)` → `connect(self, is_reconnect=False)`** — upstream bug:
  `gateway/run.py` passes `is_reconnect=` so QQ never connects at all
  without this.
- `_convert_audio_to_qq_silk(path)`: `ffmpeg → s16le mono 24 kHz PCM →
  pilk.encode(tencent=True)` (binary `silk_v3_encoder -tencent` fallback).
- `send_voice()`: converts local audio to SILK before `_send_media(...,
  MEDIA_TYPE_VOICE, ...)`. QQ's `file_type=3` only plays SILK — a raw MP3
  upload degrades to a file attachment on the receiving client.

### 2. `tools/send_message_tool.py` (multi-bubble)

- `_send_qqbot_with_media()`: text chunks + per-file dispatch (audio →
  `send_voice`, image → `send_image_file`, video → `send_video`, else
  `send_document`) through the **live gateway adapter**.
- Routed from `_send_to_platform()` before the "non-media platforms" block.
  Without this, QQ multi-message sends silently dropped audio (the old
  "first N arrive as MP3 files" symptom).

### 3. `gateway/platforms/weixin.py`

- SILK helpers (`_convert_audio_to_weixin_silk`, 16 kHz;
  `_probe_audio_duration_ms`).
- `send_voice()`: file-attachment fallback by default;
  `WEIXIN_NATIVE_VOICE=1` switches to the SILK bubble path.
- **Real bug fix**: `_send_file()` now checks the iLink `ret`/`errcode` of
  the media `sendmessage` response (text already did) — before this,媒体
  rejections were silently swallowed.
- Upload prefers the `upload_param`-constructed CDN URL (the path used by
  every working implementation) over `upload_full_url`.
- `voice_item` mirrors a real inbound voice message exactly (see below).
- `send_weixin_direct()`: audio media routes through `send_voice` in both
  the live-adapter and ephemeral loops.

## The Weixin Voice-Bubble Investigation (read before retrying)

Verified against a live inbound voice message (dumped via a temporary
schema logger in `_download_voice`):

```json
{"type": 3, "is_completed": true, "voice_item": {
   "media": {"encrypt_query_param": "...", "aes_key": "<44 chars>", "full_url": "..."},
   "encode_type": 4, "bits_per_sample": 16, "sample_rate": 16000,
   "playtime": 2871, "text": "..."}}
```

- Payload magic is `\x02#!SILK_V3` — WeChat voice **is Tencent SILK**, and
  **`encode_type=4` means SILK in iLink** (the proto-comment enum claiming
  4=speex is wrong).
- `sample_rate` is 16000, `aes_key` is 44 chars = base64 of the 32-char hex
  string (matching upstream's `aes_key_for_api`), media has **no
  `encrypt_type`**.

Outbound messages replicating ALL of this (payload, every field,
`is_completed`, timestamps, upload via `upload_param`) are **accepted by the
server (`ret=0`) but never rendered by the client**. Upstream Hermes hit the
same wall and reverted its native-voice attempt in Apr 2026 ("not
proven-working"); Tencent's own `openclaw-weixin` plugin implements only
inbound voice. Conclusion: current WeChat clients do not render voice items
in bot messages. The plumbing is kept behind `WEIXIN_NATIVE_VOICE=1` so it
can be re-tested cheaply after WeChat/iLink updates.

Other hard-won iLink facts:

- iLink `ret=-2` is *not* only rate-limiting: it is also returned for stale
  sessions and for **proactive pushes ~30 min after the user's last
  message** (the push window). Test bot sends by messaging the bot first.
- Never create a second iLink session (out-of-process send) while the
  gateway runs — same-token concurrent sessions poison sends for a while.
- A zero/missing `playtime` produces a "0秒" bubble that STT can transcribe
  but that will not play (v1 finding; still true).

## Getting Multiple Voice Bubbles Per Reply (QQ)

Delivery-side is handled by the patches. Tell the agent (SOUL.md /
personality / direct message):

> 语音回复时：把回复拆成多条简短口语化句子，每条分别调用 text_to_speech
> 生成音频，把所有 MEDIA: 标签都放进最终回复（或用 send_message 工具逐条
> 发送）；不要合成一整条长音频。

Each audio `MEDIA:` tag dispatches through `send_voice` → one bubble each.

## Dependencies

- `ffmpeg` + `ffprobe` (apt)
- `pilk` (pip, into the Hermes venv)
- `silk_v3_encoder` built from https://github.com/areCodeOI/silk-v3-encoder
  (`make lib && make encoder`), installed to `~/.hermes/bin/`; usage text
  must mention `-tencent`.

## Verification Checklist

- [ ] Installer ends with `all patches applied` and `weixin SILK pipeline OK`.
- [ ] `hermes gateway restart` from outside any chat; log shows
      `✓ qqbot connected` (fails with `unexpected keyword argument
      'is_reconnect'` on unpatched installs).
- [ ] QQ: "用两条语音回复我" → two playable voice bubbles, no MP3 files.
- [ ] Weixin: voice replies arrive as playable MP3 file attachments
      (bubbles only if `WEIXIN_NATIVE_VOICE=1` AND Tencent has enabled
      bot-voice rendering).

## Known Failure Modes

- **QQ gateway won't connect** → `connect()` missing `is_reconnect`
  (re-run installer).
- **QQ voice arrives as file** → pilk missing AND binary encoder missing.
- **Worked before, broke later** → a Hermes update overwrote the patched
  files; re-run the installer (idempotent).
- **Weixin sends nothing at all for voice** → someone set
  `WEIXIN_NATIVE_VOICE=1`; unset it (client-side rendering still off).
