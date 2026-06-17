---
name: weixin-voice-bubble-setup
description: Use when a Hermes Agent Weixin/WeChat gateway needs native TTS voice bubbles instead of MP3/file attachments. Installs Tencent-compatible SILK v3 encoding, patches the Weixin adapter, and verifies playtime handling.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, weixin, wechat, voice, tts, silk]
    related_skills: [hermes-agent]
---

# Weixin Voice Bubble Setup

## Overview

Hermes can generate TTS audio as MP3, but Weixin iLink native voice bubbles do not reliably play plain MP3, WAV, PCM, or generic AMR-NB payloads. The working path is Tencent-compatible SILK v3 plus a non-zero `voice_item.playtime`.

The symptom before this fix is confusing: the receiver may see a voice bubble, and WeChat speech-to-text may even transcribe the message, but the bubble displays `0秒` and cannot play. That means the audio payload is recognizable server-side, but the client-side voice metadata is incomplete.

## When to Use

- User wants Hermes Weixin/WeChat outbound TTS as native voice bubbles.
- Weixin voice messages arrive as files/MP3 instead of bubbles.
- Weixin bubble appears but shows `0秒` and cannot play.
- Weixin bubble can be transcribed but has no audible playback.
- Do not use for Telegram/Feishu/QQ: their voice/audio APIs have different formats and semantics.

## Required Result

A working Weixin voice-bubble send path does all of this:

1. Generate TTS MP3 as usual.
2. Convert MP3 to 24kHz mono signed 16-bit little-endian PCM with `ffmpeg`.
3. Encode PCM to Tencent-compatible SILK v3 with `silk_v3_encoder -tencent`.
4. Send the `.silk` file through Weixin iLink as `MEDIA_VOICE` + `ITEM_VOICE`.
5. Populate `voice_item` with:
   - `encode_type = 6`
   - `sample_rate = 24000`
   - `bits_per_sample = 16`
   - `playtime = <duration milliseconds>`

## One-Shot Setup

Use the bundled helper script if available:

```bash
python3 ~/.hermes/skills/software-development/weixin-voice-bubble-setup/scripts/install_weixin_voice.py
```

If the script was copied somewhere else:

```bash
python3 /path/to/install_weixin_voice.py
```

Then restart the gateway from a normal shell outside the gateway process:

```bash
hermes gateway restart
# or
systemctl --user restart hermes-gateway
```

Do not try to restart the gateway from inside a Weixin gateway chat. Hermes blocks this to prevent restart loops.

## One-Shot Weixin + ElevenLabs Setup

For a fresh Hermes installation where you want both native Weixin voice bubbles and ElevenLabs TTS, copy this whole skill directory to the new machine, then run:

```bash
ELEVENLABS_API_KEY=your_key_here \
python3 ~/.hermes/skills/software-development/weixin-voice-bubble-setup/scripts/setup_weixin_elevenlabs_voice.py \
  --voice-id your_elevenlabs_voice_id \
  --model-id eleven_multilingual_v2 \
  --restart
```

Use `eleven_flash_v2_5` instead of `eleven_multilingual_v2` if you prefer lower latency and a more conversational feel. Use `eleven_multilingual_v2` if Chinese or mixed-language pronunciation matters more.

The setup script does four things:

1. Writes `ELEVENLABS_API_KEY` to `~/.hermes/.env` if provided.
2. Sets `tts.provider = elevenlabs`, `tts.elevenlabs.voice_id`, and `tts.elevenlabs.model_id` in `~/.hermes/config.yaml`.
3. Runs `uv sync --extra tts-premium` in the Hermes checkout so the `elevenlabs` Python package is available.
4. Runs `install_weixin_voice.py` to install the Tencent-compatible SILK encoder and patch `gateway/platforms/weixin.py`.

If the new machine already has dependencies or you only want part of the setup:

```bash
python3 setup_weixin_elevenlabs_voice.py --skip-deps
python3 setup_weixin_elevenlabs_voice.py --skip-weixin-patch
```

After setup, send a real TTS message through Weixin and verify it arrives as a playable native voice bubble, not a file attachment.

## Manual Encoder Install

If doing it by hand on Linux:

```bash
sudo apt-get update
sudo apt-get install -y git build-essential ffmpeg
rm -rf /tmp/silk-v3-encoder
git clone --depth 1 https://github.com/areCodeOI/silk-v3-encoder.git /tmp/silk-v3-encoder
cd /tmp/silk-v3-encoder/silk
make lib
make encoder
mkdir -p ~/.hermes/bin ~/.hermes/vendor/silk-v3
cp encoder ~/.hermes/bin/silk_v3_encoder
chmod +x ~/.hermes/bin/silk_v3_encoder
```

Verify:

```bash
~/.hermes/bin/silk_v3_encoder 2>&1 | head
```

The usage text should mention `-tencent`.

## Adapter Patch

Patch `gateway/platforms/weixin.py` in the Hermes checkout.

Add constants near the Weixin constants:

```python
SILK_ENCODER_PATH = get_hermes_home() / "bin" / "silk_v3_encoder"
WEIXIN_VOICE_SAMPLE_RATE = 24000
```

Add `_convert_audio_to_weixin_silk(audio_path)`:

- No-op for `.silk` input.
- Convert other audio to PCM using:
  ```bash
  ffmpeg -y -v error -i input.mp3 -f s16le -ac 1 -ar 24000 output.pcm
  ```
- Encode with:
  ```bash
  silk_v3_encoder output.pcm output.mp3.silk -Fs_API 24000 -Fs_maxInternal 24000 -packetlength 20 -rate 25000 -tencent -quiet
  ```
- Return the `.silk` path.

Add `_probe_audio_duration_ms(path)`:

- For `*.mp3.silk`, check the original `*.mp3` first via `ffprobe`.
- Return duration in milliseconds.
- As fallback, estimate SILK duration by counting 20ms frames after the `#!SILK_V3` header.

Update `send_voice()` so it sends the converted SILK path:

```python
silk_path = _convert_audio_to_weixin_silk(audio_path) or audio_path
message_id = await self._send_file(
    chat_id,
    silk_path,
    caption or "",
    force_file_attachment=False,
)
```

Update the `.silk` branch in `_send_file()`:

```python
if media_type == MEDIA_VOICE and path.endswith(".silk"):
    item_kwargs["encode_type"] = 6
    item_kwargs["sample_rate"] = WEIXIN_VOICE_SAMPLE_RATE
    item_kwargs["bits_per_sample"] = 16
    playtime_ms = _probe_audio_duration_ms(path)
    if playtime_ms:
        item_kwargs["playtime"] = playtime_ms
```

The existing `_outbound_media_builder()` must preserve `voice_item.playtime`:

```python
"voice_item": {
    "media": {...},
    "encode_type": kw.get("encode_type"),
    "bits_per_sample": kw.get("bits_per_sample"),
    "sample_rate": kw.get("sample_rate"),
    "playtime": kw.get("playtime", 0),
}
```

## Why Other Formats Failed

- MP3 can render a bubble but does not reliably play as a Weixin voice item.
- WAV/PCM can render a bubble but usually produce no audio.
- Generic AMR-NB can be structurally valid and still decode as noise in WeChat.
- `pysilk==0.0.1` from PyPI is an empty metadata package and not a usable encoder.
- Standard ffmpeg builds usually do not include a SILK encoder.
- `libopencore-amrwb` may expose decoder symbols only, not encoder symbols.

## Verification Checklist

- [ ] `~/.hermes/bin/silk_v3_encoder` exists and is executable.
- [ ] `ffmpeg` and `ffprobe` exist on PATH.
- [ ] TTS MP3 conversion creates a file whose bytes start with `#!SILK_V3` or `\x02#!SILK_V3`.
- [ ] `_probe_audio_duration_ms("some.mp3.silk")` returns a non-zero duration.
- [ ] Gateway has been restarted from outside the gateway process.
- [ ] Weixin receives a native voice bubble, not a file attachment.
- [ ] Bubble displays a real duration, not `0秒`.
- [ ] Bubble plays audio, and speech-to-text still works.

## Official PR Guidance

For upstream Hermes, avoid bundling a third-party SILK binary directly unless licensing is reviewed. A cleaner PR shape is:

1. Add optional Weixin config keys, e.g. `platforms.weixin.extra.silk_encoder_path` or environment variable `WEIXIN_SILK_ENCODER`.
2. Add MP3/WAV-to-SILK conversion only when the encoder exists.
3. Keep a safe fallback when no encoder is configured.
4. Add duration probing and always set `playtime` for voice items.
5. Add docs explaining that Weixin voice bubbles require Tencent-compatible SILK v3.
6. Add tests for path selection, duration calculation, and `voice_item` payload fields without requiring live Weixin.

The PR title can be:

```text
fix(weixin): encode outbound voice messages as Tencent SILK v3
```

Mention in the PR body that the key bug was `0秒` voice bubbles: WeChat STT could recognize the audio, but the client would not play without a valid `playtime`.
