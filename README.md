# Hermes Weixin Voice Bubble

Native Weixin/WeChat voice bubbles for Hermes Agent.

Hermes can already generate TTS audio, but Weixin iLink does not reliably play ordinary MP3/WAV/PCM payloads as native voice messages. This installer patches the Weixin adapter so outbound TTS audio is converted to Tencent-compatible SILK v3 and sent as a real Weixin voice bubble.

This project is **not tied to ElevenLabs**. It works with whatever TTS provider your Hermes uses — Edge, OpenAI, MiniMax, ElevenLabs, Mistral, Gemini, local TTS, etc. The only job here is the Weixin delivery format.

## What it fixes

Before this patch, Weixin voice output may show up as:

- an MP3/file attachment instead of a voice bubble
- a voice bubble that shows `0秒`
- a bubble that WeChat can transcribe but cannot play

The working format is:

1. Generate normal TTS audio, usually MP3.
2. Convert it to 24kHz mono PCM.
3. Encode it as Tencent-compatible SILK v3.
4. Send it through Weixin iLink as `MEDIA_VOICE` / `ITEM_VOICE`.
5. Include real voice metadata, especially non-zero `playtime`.

## Quick install

On the Hermes machine:

```bash
sudo apt-get update
sudo apt-get install -y git build-essential ffmpeg

python3 scripts/install_weixin_voice.py
hermes gateway restart
```

If you copied only the script:

```bash
python3 /path/to/install_weixin_voice.py
hermes gateway restart
```

The script assumes the default Hermes layout:

```text
~/.hermes/hermes-agent/
~/.hermes/config.yaml
~/.hermes/.env
```

If your checkout is elsewhere, set env vars:

```bash
HERMES_HOME=/path/to/.hermes HERMES_REPO=/path/to/hermes-agent python3 scripts/install_weixin_voice.py
```

## What the installer does

The installer:

- clones and builds `silk-v3-encoder`
- installs the encoder at `~/.hermes/bin/silk_v3_encoder`
- patches `gateway/platforms/weixin.py`
- backs up the original adapter as `weixin.py.bak-weixin-voice`
- adds audio conversion from MP3/WAV/etc. to Tencent SILK v3
- adds duration probing so Weixin gets a real `playtime`

The important Weixin voice fields are:

```python
encode_type = 6
sample_rate = 24000
bits_per_sample = 16
playtime = <duration milliseconds>
```

`playtime` is critical. Without it, WeChat may show a `0秒` bubble that can be transcribed but cannot be played.

## TTS provider setup

This repository does not configure your TTS provider.

Use normal Hermes config for TTS, for example:

```yaml
tts:
  provider: edge
```

or:

```yaml
tts:
  provider: elevenlabs
  elevenlabs:
    voice_id: your_voice_id
    model_id: eleven_multilingual_v2
```

The Weixin patch sits after TTS generation. Any provider that produces an audio file can be delivered as a native Weixin voice bubble.

## Verify

After restarting the gateway, send a TTS message through Weixin and check:

- it arrives as a native voice bubble, not a file
- it shows a real duration, not `0秒`
- it plays audio
- WeChat speech-to-text still works

You can also verify the encoder exists:

```bash
~/.hermes/bin/silk_v3_encoder 2>&1 | head
```

The usage text should mention `-tencent`.

## Files

```text
scripts/install_weixin_voice.py          # installer / patcher
docs/WEIXIN_VOICE_BUBBLE_SKILL.md        # detailed Hermes skill notes
```

## Notes

This is a practical patch for existing Hermes installs. For upstream Hermes, the cleaner long-term version would make the SILK encoder path configurable and keep a safe fallback when no encoder is installed.

## License

MIT
