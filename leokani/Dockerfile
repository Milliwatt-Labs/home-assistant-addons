# syntax=docker/dockerfile:1
# Leokani for Home Assistant: the Wyoming server (and the optional OpenAI-compatible speech API) of
# release/leokani-server, on Home Assistant's Debian base image with bashio and s6-overlay.
#
# Every RUN happens on the build machine's own architecture: espeak-ng is cross-compiled, Python is a prebuilt
# standalone build for the target, and the wheels are fetched for the target. The final stage is copies only, so
# building amd64 on an arm64 machine (or the reverse) needs no emulation.

ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
ARG TARGETARCH
ARG PYTHON_IMAGE=python:3.12-slim-trixie

# ---------------------------------------------------------------------------------------- downloads, checksummed
# Python 3.12 from python-build-standalone (only needs glibc).
FROM scratch AS python-amd64
ADD --checksum=sha256:ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20 \
    https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz \
    /python.tar.gz
FROM scratch AS python-arm64
ADD --checksum=sha256:bb4b6392670c5d3f93876996fcaa19858a4a413a0c7720144c18b0d6f1f0db2d \
    https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz \
    /python.tar.gz
FROM python-${TARGETARCH} AS python-download

# The Leokani-tts 7M voice (CC BY-NC 4.0), pinned to a Hugging Face commit.
FROM scratch AS model
ARG HF=https://huggingface.co/milliwattlabs/leokani-tts-7M/resolve/7c490eed4e14f5c6ff42bbf943956c788feca2a8
ADD --checksum=sha256:cb852e09b0341d58608e858cfaed5c53275bd4277baa1bf8e6dad4c20efcef0d ${HF}/leokani-tts-fp16.onnx /
ADD --checksum=sha256:9457d15f82bb7f183b56ae96c62daa4635d03e7122a11d623f06a7681a387cff ${HF}/config.json /
ADD --checksum=sha256:ce4578108c9d71098635677f94db48963b4535bdf0afb2fac6245cc1a7328c76 ${HF}/leokani_onnx.py /
ADD --checksum=sha256:41003d4a74749c0220e33dd415042164b5a1093ed401f36277234f772d22d3d0 ${HF}/LICENSE /

# ---------------------------------------------------------------------------------------- espeak-ng library
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS espeak
ARG TARGETARCH
ARG BUILDARCH
ARG ESPEAK_COMMIT=8269af044a94273f030fbfd3fa9033dd664db578
RUN set -eux; \
    case "$TARGETARCH" in amd64) triple=x86_64-linux-gnu; pkg=x86-64-linux-gnu; dev=amd64;; arm64) triple=aarch64-linux-gnu; pkg=aarch64-linux-gnu; dev=arm64;; \
      *) echo "unsupported $TARGETARCH"; exit 1;; esac; \
    pkgs="ca-certificates git cmake make"; \
    if [ "$TARGETARCH" = "$BUILDARCH" ]; then pkgs="$pkgs gcc g++ libc6-dev"; \
    else pkgs="$pkgs gcc-$pkg g++-$pkg libc6-dev-$dev-cross"; fi; \
    apt-get update; apt-get install -y --no-install-recommends $pkgs; rm -rf /var/lib/apt/lists/*; \
    echo "$triple" > /triple
RUN git clone --filter=blob:none https://github.com/espeak-ng/espeak-ng /src \
    && git -C /src checkout -q "$ESPEAK_COMMIT"
RUN set -eux; triple=$(cat /triple); \
    if [ "$TARGETARCH" = "$BUILDARCH" ]; then cc=gcc; cxx=g++; else cc="$triple-gcc"; cxx="$triple-g++"; fi; \
    cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="$cc" -DCMAKE_CXX_COMPILER="$cxx" -DCMAKE_SYSTEM_NAME=Linux \
      -DCMAKE_SYSTEM_PROCESSOR="${triple%%-*}" -DBUILD_SHARED_LIBS=ON -DUSE_MBROLA=OFF -DUSE_LIBSONIC=OFF \
      -DUSE_LIBPCAUDIO=OFF -DUSE_ASYNC=OFF -DUSE_KLATT=OFF -DUSE_SPEECHPLAYER=OFF -DENABLE_TESTS=OFF; \
    cmake --build /build --target espeak-ng -j"$(nproc)"; \
    mkdir -p /out/lib /out/share; \
    cp -L "$(find /build -name 'libespeak-ng.so.1' | head -1)" /out/lib/libespeak-ng.so.1; \
    "$triple-strip" /out/lib/libespeak-ng.so.1 2>/dev/null || strip /out/lib/libespeak-ng.so.1 || true; \
    cp /src/COPYING /out/share/espeak-ng-COPYING; echo "$ESPEAK_COMMIT" > /out/share/espeak-ng-COMMIT

# ---------------------------------------------------------------------------------------- Python and packages
FROM --platform=$BUILDPLATFORM $PYTHON_IMAGE AS python
ARG TARGETARCH
COPY --from=python-download /python.tar.gz /tmp/python.tar.gz
COPY requirements.txt /tmp/requirements.txt
RUN set -eux; mkdir -p /opt/leokani; tar -xzf /tmp/python.tar.gz -C /opt/leokani; \
    py=/opt/leokani/python; lib=$py/lib/python3.12; \
    rm -rf $lib/test $lib/idlelib $lib/tkinter $lib/turtledemo $lib/ensurepip $lib/lib2to3 $lib/pydoc_data \
      $lib/site-packages/pip* $py/lib/libtcl* $py/lib/libtk* $py/lib/tcl* $py/lib/tk* $py/include $py/share \
      $py/bin/idle3* $py/bin/pip* $py/bin/pydoc3*; \
    case "$TARGETARCH" in amd64) m=x86_64;; arm64) m=aarch64;; esac; \
    pip install --no-cache-dir --disable-pip-version-check --target $lib/site-packages --only-binary=:all: \
      --implementation cp --python-version 3.12 \
      --platform "manylinux_2_28_$m" --platform "manylinux_2_27_$m" --platform "manylinux_2_17_$m" \
      --platform "manylinux2014_$m" --platform "manylinux_2_34_$m" -r /tmp/requirements.txt; \
    find $py -name '__pycache__' -prune -exec rm -rf {} +

# ---------------------------------------------------------------------------------------- the app
FROM ${BUILD_FROM}
COPY --from=python /opt/leokani/python /opt/leokani/python
COPY --from=espeak /out/lib/libespeak-ng.so.1 /opt/leokani/espeak/lib/
COPY --from=espeak /out/share/ /usr/share/doc/espeak-ng/
COPY espeak-ng-data/ /opt/leokani/espeak/espeak-ng-data/
COPY --from=model / /opt/leokani/model/
COPY leokani_server/ /opt/leokani/app/leokani_server/
COPY rootfs /
ENV PATH=/opt/leokani/python/bin:$PATH \
    PYTHONPATH=/opt/leokani/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEOKANI_MODEL_DIR=/opt/leokani/model \
    LEOKANI_ESPEAK_LIB=/opt/leokani/espeak/lib/libespeak-ng.so.1 \
    ESPEAK_DATA_PATH=/opt/leokani/espeak
LABEL org.opencontainers.image.title="Leokani" \
      org.opencontainers.image.source="https://github.com/Milliwatt-Labs/home-assistant-addons" \
      org.opencontainers.image.description="Leokani-tts 7M text to speech for Home Assistant" \
      org.opencontainers.image.vendor="Milliwatt Labs" \
      org.opencontainers.image.licenses="MIT AND CC-BY-NC-4.0 AND GPL-3.0-or-later"

# A Wyoming describe/info round trip, not a bare port check.
HEALTHCHECK --start-period=2m --interval=30s --timeout=10s --retries=3 \
    CMD ["python3", "-m", "leokani_server", "healthcheck"]
