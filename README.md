# Hermes Weixin Voice Bubble / Hermes 微信原生语音气泡

[中文](#中文说明) | [English](#english)

---

## ⚡ v2（推荐 / Recommended）

**v2 实现 QQ 原生语音气泡 + 一次回复多条语音气泡（类真人聊天），已真机验证**；微信端提供可播放的 MP3 文件兜底，气泡管线完整保留在 `WEIXIN_NATIVE_VOICE=1` 开关后面（原因见下）。适配 2026-06 之后的新版 Hermes（v1 脚本的锚点已全部失效）。

```bash
python3 scripts/install_voice_bubble_v2.py
hermes gateway restart   # 在普通 shell 里执行，不要在聊天里让 AI 重启
```

v2 修的四个层面：

1. **QQ 连接**（`gateway/platforms/qqbot/adapter.py`）：上游 bug——`connect()` 缺 `is_reconnect` 参数，QQ 网关根本连不上。
2. **QQ 气泡**：QQ 官方 API 的 `file_type=3` 只认 SILK，MP3 直接上传会降级成文件。v2 在发送前用 `pilk`（备选 `silk_v3_encoder -tencent`）转 SILK。✅ 已验证：多条语音全部以气泡形式送达。
3. **一次多条**（`send_message` 工具 + `send_weixin_direct`）：中途分条发送的音频原来在微信走文档、在 QQ 直接被丢弃——这就是"前几条是 MP3 文件，最后一条才是气泡"的原因。
4. **微信媒体发送静默失败**：`_send_file` 原来不检查 iLink 业务返回码，媒体被服务端拒绝时无声无息。v2 补上检查和日志。

**⚠️ 微信 bot 语音气泡的现状（2026-07 实测）**：即使发送与真实语音消息逐字节一致的 SILK 载荷和逐字段一致的 `voice_item`（encode_type=4、16kHz、playtime、无 encrypt_type、is_completed），iLink 服务端返回成功但**微信客户端不渲染**。Hermes 官方 2026-04 的原生语音尝试也因此撤回，腾讯自家 openclaw-weixin 插件只做了收不做发。结论：当前微信客户端不渲染 bot 语音消息，等腾讯放开后设置 `WEIXIN_NATIVE_VOICE=1` 即可切回气泡模式。完整排查记录见 [docs/VOICE_BUBBLE_SKILL_V2.md](docs/VOICE_BUBBLE_SKILL_V2.md)。

让 AI 真正"像真人一样发多条语音"还需要提示词配合（写进 SOUL.md 或直接在聊天里要求）：

> 语音回复时：把回复拆成多条简短口语化句子，每条分别调用 text_to_speech 生成音频，把所有 MEDIA: 标签都放进回复；不要合成一整条长音频。

**v2: QQ native voice bubbles + multi-bubble replies (verified live); Weixin falls back to playable MP3 attachments because current WeChat clients do not render bot voice items** (full plumbing kept behind `WEIXIN_NATIVE_VOICE=1`; the investigation — byte-exact payload replica accepted by the server but never rendered — is documented in [docs/VOICE_BUBBLE_SKILL_V2.md](docs/VOICE_BUBBLE_SKILL_V2.md)).

以下为 v1 文档（历史参考；其"微信气泡成功"结论在当前微信客户端上已不可复现）/ v1 docs below (historical; its Weixin-bubble success no longer reproduces on current WeChat clients):

---

## 中文说明

这是给 Hermes Agent 的微信 / Weixin 网关用的原生语音气泡补丁。

Hermes 本身可以生成 TTS 音频，但微信 iLink 不一定能把普通 MP3、WAV、PCM 当成真正的微信语音消息播放。最常见的问题是：看起来像语音气泡，但显示 `0秒`，微信甚至能识别文字，可是点不开、播不出来。

这个项目解决的是“微信发送格式”的问题：把 Hermes 生成的 TTS 音频转换成微信客户端能播放的 Tencent-compatible SILK v3，然后作为真正的微信语音气泡发出去。

它不绑定 ElevenLabs。

你用 Edge TTS、OpenAI TTS、MiniMax、ElevenLabs、Mistral、Gemini、本地 TTS 都可以。只要 Hermes 生成了音频，这个补丁负责把它变成微信能播放的原生语音气泡。

### 它修复什么

安装前，Hermes 的微信语音回复可能会变成：

- MP3 文件附件，而不是语音气泡
- 语音气泡显示 `0秒`
- 微信能转文字，但语音点不开
- 气泡存在，但没有声音

安装后，发送链路变成：

1. Hermes 生成普通 TTS 音频，通常是 MP3。
2. 用 ffmpeg 转成 24kHz、单声道、16-bit PCM。
3. 用 `silk_v3_encoder -tencent` 转成 Tencent-compatible SILK v3。
4. 通过 Weixin iLink 作为 `MEDIA_VOICE` / `ITEM_VOICE` 发送。
5. 给微信补上真实的语音参数，尤其是 `playtime`。

`playtime` 很关键。没有它，微信很容易显示 `0秒`，然后语音气泡不能播放。

### 快速安装

在 Hermes 所在机器上执行：

```bash
sudo apt-get update
sudo apt-get install -y git build-essential ffmpeg

git clone https://github.com/Evaoooos/hermes-weixin-voice-bubble.git
cd hermes-weixin-voice-bubble

python3 scripts/install_weixin_voice.py
hermes gateway restart
```

如果你只复制了脚本，也可以这样：

```bash
python3 /path/to/install_weixin_voice.py
hermes gateway restart
```

脚本默认 Hermes 路径是：

```text
~/.hermes/hermes-agent/
~/.hermes/config.yaml
~/.hermes/.env
```

如果你的 Hermes 不在默认位置，可以指定：

```bash
HERMES_HOME=/path/to/.hermes \
HERMES_REPO=/path/to/hermes-agent \
python3 scripts/install_weixin_voice.py
```

### 安装脚本做了什么

`scripts/install_weixin_voice.py` 会自动：

- 克隆并编译 `silk-v3-encoder`
- 安装编码器到 `~/.hermes/bin/silk_v3_encoder`
- 修改 Hermes 的 `gateway/platforms/weixin.py`
- 备份原文件为 `weixin.py.bak-weixin-voice`
- 在微信发送语音前，把 MP3 / WAV 等音频转成 Tencent SILK v3
- 用 `ffprobe` 获取原音频时长
- 给微信 `voice_item` 补上 `playtime`

关键参数是：

```python
encode_type = 6
sample_rate = 24000
bits_per_sample = 16
playtime = <duration milliseconds>
```

### TTS 怎么配置

这个仓库不负责配置 TTS 供应商，只负责微信气泡语音格式。

比如你可以继续用 Edge：

```yaml
tts:
  provider: edge
```

也可以用 ElevenLabs：

```yaml
tts:
  provider: elevenlabs
  elevenlabs:
    voice_id: your_voice_id
    model_id: eleven_multilingual_v2
```

也可以用 OpenAI、MiniMax、Mistral 等。这个补丁在 TTS 生成音频之后工作，所以不关心你前面用的是谁。

### 如何验证

重启 gateway 后，让 Hermes 在微信里发一条 TTS 语音，检查：

- 收到的是微信原生语音气泡，不是文件附件
- 气泡显示真实秒数，不是 `0秒`
- 能正常播放
- 微信语音转文字也正常

也可以检查编码器是否安装成功：

```bash
~/.hermes/bin/silk_v3_encoder 2>&1 | head
```

输出里应该能看到 `-tencent` 相关用法。

### 文件说明

```text
scripts/install_weixin_voice.py          # 安装 / patch 脚本
docs/WEIXIN_VOICE_BUBBLE_SKILL.md        # 详细 Hermes skill 文档
```

### 注意

这是一个给现有 Hermes 安装使用的实用补丁。

如果要合进 Hermes 官方，更理想的做法是把 SILK encoder 路径做成配置项，并且在没有 encoder 时保留安全 fallback。

---

## English

Native Weixin / WeChat voice bubbles for Hermes Agent.

Hermes can already generate TTS audio, but Weixin iLink does not reliably play ordinary MP3, WAV, or PCM payloads as native voice messages. A common failure mode is a voice bubble that shows `0秒`: WeChat may even transcribe the message, but the bubble cannot be played.

This project fixes the Weixin delivery format. It converts Hermes-generated TTS audio into Tencent-compatible SILK v3 and sends it as a real Weixin voice bubble.

It is not tied to ElevenLabs.

You can use Edge TTS, OpenAI TTS, MiniMax, ElevenLabs, Mistral, Gemini, local TTS, or any other Hermes TTS provider. As long as Hermes produces an audio file, this patch handles the Weixin voice-bubble delivery format.

### What it fixes

Before this patch, Weixin voice output may appear as:

- an MP3/file attachment instead of a voice bubble
- a voice bubble showing `0秒`
- a bubble that WeChat can transcribe but cannot play
- a bubble with no audible playback

After installation, the send path becomes:

1. Hermes generates normal TTS audio, usually MP3.
2. ffmpeg converts it to 24kHz mono 16-bit PCM.
3. `silk_v3_encoder -tencent` encodes it as Tencent-compatible SILK v3.
4. Weixin iLink sends it as `MEDIA_VOICE` / `ITEM_VOICE`.
5. Hermes includes proper voice metadata, especially a non-zero `playtime`.

`playtime` is critical. Without it, WeChat may show a `0秒` bubble that cannot be played.

### Quick install

On the Hermes machine:

```bash
sudo apt-get update
sudo apt-get install -y git build-essential ffmpeg

git clone https://github.com/Evaoooos/hermes-weixin-voice-bubble.git
cd hermes-weixin-voice-bubble

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
HERMES_HOME=/path/to/.hermes \
HERMES_REPO=/path/to/hermes-agent \
python3 scripts/install_weixin_voice.py
```

### What the installer does

`scripts/install_weixin_voice.py` automatically:

- clones and builds `silk-v3-encoder`
- installs the encoder at `~/.hermes/bin/silk_v3_encoder`
- patches Hermes `gateway/platforms/weixin.py`
- backs up the original file as `weixin.py.bak-weixin-voice`
- converts MP3 / WAV / other audio to Tencent SILK v3 before sending
- probes audio duration with `ffprobe`
- fills `voice_item.playtime` for Weixin

Important Weixin voice fields:

```python
encode_type = 6
sample_rate = 24000
bits_per_sample = 16
playtime = <duration milliseconds>
```

### TTS provider setup

This repository does not configure your TTS provider. It only fixes the Weixin voice-bubble format.

For example, Edge TTS:

```yaml
tts:
  provider: edge
```

Or ElevenLabs:

```yaml
tts:
  provider: elevenlabs
  elevenlabs:
    voice_id: your_voice_id
    model_id: eleven_multilingual_v2
```

OpenAI, MiniMax, Mistral, and other providers are also fine. This patch runs after TTS audio generation, so it does not care which provider created the audio.

### Verify

After restarting the gateway, send a TTS message through Weixin and check:

- it arrives as a native Weixin voice bubble, not a file attachment
- it shows a real duration, not `0秒`
- it plays audio
- WeChat speech-to-text still works

You can also verify the encoder:

```bash
~/.hermes/bin/silk_v3_encoder 2>&1 | head
```

The usage text should mention `-tencent`.

### Files

```text
scripts/install_weixin_voice.py          # installer / patcher
docs/WEIXIN_VOICE_BUBBLE_SKILL.md        # detailed Hermes skill notes
```

### Note

This is a practical patch for existing Hermes installs.

For upstream Hermes, a cleaner long-term implementation would make the SILK encoder path configurable and keep a safe fallback when no encoder is installed.

## License

MIT
