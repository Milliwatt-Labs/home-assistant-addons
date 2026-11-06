# Leokani

Leokani gives Home Assistant a fast, natural English voice that runs on your own hardware. Nothing you say or
hear leaves your network.

## Set up

1. Install Leokani and start it.
2. Go to **Settings > Devices & services**. Under **Discovered**, choose **Add** on **Leokani**, then **Submit**.
3. Go to **Settings > Voice assistants**, open your assistant, and choose **leokani** under **Text-to-speech**.

## Try it

In **Developer tools > Actions**:

```yaml
action: tts.speak
target:
  entity_id: tts.leokani
data:
  media_player_entity_id: media_player.kitchen
  message: Dinner is ready.
```

## Options

| Option | |
|---|---|
| Model | Leokani-tts 7M (the default) or Leokani-tts 4.5M, a smaller voice. |
| Speed | Speaking rate, from 0.5 to 2.0. 1.0 is normal. |
| API key | Key the OpenAI-compatible speech API asks for. Empty: no key. |
| Debug logging | Log every request. |

## OpenAI-compatible speech API

Other software that can use OpenAI's speech API can use Leokani too. It's off until you give it a port: in the
**Configuration** tab, under **Network**, enter a port (for example `8880`) for **OpenAI-compatible speech API**
and restart the app. Then point the software at `http://HOME_ASSISTANT_IP:8880/v1`:

```bash
curl http://HOME_ASSISTANT_IP:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model": "leokani-tts", "voice": "leokani", "input": "Hello there!"}' \
  -o hello.mp3
```

If you set an API key, send it as `Authorization: Bearer KEY`. Formats: `mp3` (default), `opus`, `aac`, `flac`,
`wav` and `pcm`. More at [milliwattlabs.com/docs/leokani/server](https://milliwattlabs.com/docs/leokani/server/).

## Hardware

64-bit only: amd64 (Intel or AMD) or aarch64 (Raspberry Pi 4 or 5, other 64-bit Arm boards). No GPU needed.

## Acceptable use

Don't use Leokani-tts, or speech made with it, to:

- impersonate a real person, or make it sound like someone said something they didn't, without their consent;
- deceive or defraud people, including scams, phishing calls and fake emergency or official messages;
- harass, threaten or bully anyone, or produce hateful or sexual content involving minors;
- spread disinformation, including fake news audio or election-related deception;
- break any law, including privacy, consumer-protection and robocall rules.

Tell people when speech is synthetic wherever the law requires it or they could reasonably be misled. You are
responsible for what you make with the model. These terms come in addition to the license.

Speech made with Leokani-tts carries an inaudible watermark, built into the model, that lets Milliwatt Labs
recognize audio it generated. The mark is the same in all audio and contains no information about you or your text.

## Licenses

- **The voices**, [Leokani-tts 7M](https://huggingface.co/milliwattlabs/leokani-tts-7M) and
  [Leokani-tts 4.5M](https://huggingface.co/milliwattlabs/leokani-tts-4.5M), are
  [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/): free for non-commercial use. For a commercial
  license or a custom voice, email [hello@milliwattlabs.com](mailto:hello@milliwattlabs.com).
- **espeak-ng**, which turns text into phonemes, is GPL-3.0. Its source is at
  [github.com/espeak-ng/espeak-ng](https://github.com/espeak-ng/espeak-ng/tree/8269af044a94273f030fbfd3fa9033dd664db578)
  (commit 8269af04). The license is in the app at `/usr/share/doc/espeak-ng/espeak-ng-COPYING`.
- **This app's own code** is MIT.
