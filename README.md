# Leokani for Home Assistant

A fast, local English voice for Home Assistant: [Leokani-tts 7M](https://huggingface.co/milliwattlabs/leokani-tts-7M)
or [Leokani-tts 4.5M](https://huggingface.co/milliwattlabs/leokani-tts-4.5M) from
[Milliwatt Labs](https://milliwattlabs.com), as a Home Assistant app. Runs on the CPU, on amd64 or aarch64
(Raspberry Pi 4 or 5).

## Install

1. In Home Assistant, go to **Settings > Apps** and choose **Install app**. Open the **⋮** menu and choose
   **Repositories**.
2. Choose **Add**, paste `https://github.com/Milliwatt-Labs/home-assistant-addons` and choose **Add**.
3. Back in the store, find **Leokani**, install it and start it.

Then follow the app's **Documentation** tab: Home Assistant finds Leokani by itself.

## Apps

- [Leokani](leokani/): text to speech over Wyoming, plus an optional OpenAI-compatible speech API.

## License

The app's code is MIT ([LICENSE](LICENSE)). The Leokani-tts voices are CC BY-NC 4.0, non-commercial use only.
espeak-ng and its data are GPL-3.0. See [leokani/DOCS.md](leokani/DOCS.md#licenses).
