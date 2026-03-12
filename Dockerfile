# --------------------------------------------------------------------------------------------------------------
# サードパーティーライブラリのダウンロードを行うステージ
# Docker のマルチステージビルドを使い、最終的な Docker イメージのサイズを抑え、ビルドキャッシュを効かせる
# --------------------------------------------------------------------------------------------------------------

FROM ubuntu:22.04 AS thirdparty-downloader

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends aria2 ca-certificates unzip xz-utils
WORKDIR /
RUN aria2c -x10 https://github.com/tsukumijima/KonomiTV/releases/download/v0.13.0/thirdparty-linux.tar.xz
RUN tar xvf thirdparty-linux.tar.xz

# --------------------------------------------------------------------------------------------------------------
# クライアントをビルドするステージ
# --------------------------------------------------------------------------------------------------------------

FROM node:20.16.0 AS client-builder

WORKDIR /code/client/
COPY ./client/package.json ./client/yarn.lock /code/client/
RUN yarn install --frozen-lockfile
COPY ./client/ /code/client/
RUN yarn build

# --------------------------------------------------------------------------------------------------------------
# Amatsukaze関連ツール (chapter_exe, join_logo_scp, logoframe) をビルド・抽出するステージ
# --------------------------------------------------------------------------------------------------------------

FROM ubuntu:22.04 AS amatsukaze-builder

ENV DEBIAN_FRONTEND=noninteractive
ENV UBUNTU_VERSION=22.04
ENV ARCH=amd64

ENV AVISYNTH_VER=3.7.5
ENV LSMASH_REV=18a9ed25c7ff79a7f4f4bf850c345c72179b8998
ENV CHAPTER_EXE_REV=32880d45f088e574285a101e6a49b032bb04f6ea
ENV JOIN_LOGO_SCP_VER=Ver4.1.0_Linux

# ビルドに必要なツール群と、L-SMASH-Works(TS読み込み)に必要な FFmpeg 開発ライブラリ(libav*)を追加
RUN apt-get update && apt-get install -y \
    build-essential git curl wget p7zip-full nasm cmake meson ninja-build \
    pkg-config autoconf automake libtool openssl zlib1g libz-dev libssl-dev \
    libavformat-dev libavcodec-dev libswscale-dev libavutil-dev libswresample-dev \
    && rm -rf /var/lib/apt/lists/*

# AviSynth+ 本体のインストール (CUDAFiltersは不整合の原因になるため除外)
RUN wget https://github.com/rigaya/AviSynthCUDAFilters/releases/download/0.7.3/avisynth_${AVISYNTH_VER}-1_${ARCH}_Ubuntu${UBUNTU_VERSION}.deb -O avisynth.deb \
    && apt-get install -y ./avisynth.deb \
    && rm ./avisynth.deb

# L-SMASH (chapter_exe と L-SMASH-Works の動作に必要な依存ライブラリ)
RUN git clone https://github.com/l-smash/l-smash.git \
    && cd l-smash \
    && git checkout ${LSMASH_REV} \
    && ./configure --enable-shared \
    && make -j$(nproc) \
    && make install

# 【修正箇所】L-SMASH-Works (AviSynth用 TS読み込みプラグイン) のビルド
# Ubuntu 22.04 (FFmpeg 4.4) と互換性を保つため、2021年11月以前のコミットをチェックアウトして確実にビルドする
RUN export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH \
    && git clone https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works.git \
    && cd L-SMASH-Works \
    && git checkout $(git rev-list -n 1 --before="2021-11-01" HEAD) \
    && cd AviSynth \
    && mkdir -p /usr/local/lib/avisynth/ \
    && if [ -f meson.build ]; then \
           meson setup build && ninja -C build && cp build/liblsmashsource.so /usr/local/lib/avisynth/LSMASHSource.so; \
       else \
           ./configure && make -j$(nproc) && cp LSMASHSource.so /usr/local/lib/avisynth/LSMASHSource.so; \
       fi

# chapter_exe (無音・シーンチェンジ検出用)
RUN git clone https://github.com/rigaya/chapter_exe \
    && cd chapter_exe \
    && git checkout ${CHAPTER_EXE_REV} \
    && cd src \
    && make -j$(nproc) \
    && install -D -t /usr/local/bin chapter_exe

# join_logo_scp (ロゴ・CM解析、チャプター生成用)
RUN git clone --depth=1 --branch ${JOIN_LOGO_SCP_VER} https://github.com/tobitti0/join_logo_scp \
    && cd join_logo_scp/src \
    && make -j$(nproc) \
    && install -D -t /usr/local/bin join_logo_scp

# logoframe (ロゴ検出用) : tobitti0氏のJoinLogoScpTrialSetLinuxサブモジュールからソースを取得してLinuxネイティブ用にビルドする
RUN git clone --recursive https://github.com/tobitti0/JoinLogoScpTrialSetLinux.git \
    && cd JoinLogoScpTrialSetLinux/modules/logoframe/src \
    && make -j$(nproc) \
    && install -D -t /usr/local/bin logoframe

# --------------------------------------------------------------------------------------------------------------
# メインのステージ
# ここで作成された実行時イメージが docker compose up -d で起動される
# --------------------------------------------------------------------------------------------------------------

FROM nvidia/cuda:12.8.0-base-ubuntu22.04

# タイムゾーンを東京に設定
ENV TZ=Asia/Tokyo

# apt-get に対話的に設定を確認されないための設定
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    # リポジトリ追加に必要な最低限のパッケージをインストール
    apt-get install -y --no-install-recommends ca-certificates curl git gpg tzdata && \
    # Intel GPU リポジトリ
    curl -fsSL https://repositories.intel.com/gpu/intel-graphics.key | gpg --yes --dearmor --output /usr/share/keyrings/intel-graphics-keyring.gpg && \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics-keyring.gpg] https://repositories.intel.com/gpu/ubuntu jammy unified' > /etc/apt/sources.list.d/intel-gpu-jammy.list && \
    # AMD / ROCm リポジトリ
    curl -fsSL https://repo.radeon.com/rocm/rocm.gpg.key | gpg --yes --dearmor --output /usr/share/keyrings/rocm-keyring.gpg && \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/rocm-keyring.gpg] https://repo.radeon.com/amdgpu/6.4.4/ubuntu jammy main' > /etc/apt/sources.list.d/amdgpu.list && \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/rocm-keyring.gpg] https://repo.radeon.com/amdgpu/6.4.4/ubuntu jammy proprietary' > /etc/apt/sources.list.d/amdgpu-proprietary.list && \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/rocm-keyring.gpg] https://repo.radeon.com/rocm/apt/6.4.4 jammy main' > /etc/apt/sources.list.d/rocm.list && \
    # Google Chrome リポジトリ
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --yes --dearmor --output /usr/share/keyrings/google-chrome-keyring.gpg && \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] https://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list && \
    # リポジトリを更新し、この時点で利用可能なパッケージをアップグレード
    apt-get update && apt-get upgrade -y && \
    # 必要なパッケージをインストール
    apt-get install -y --no-install-recommends \
        # フォント関連のライブラリ
        libfontconfig1 libfreetype6 libfribidi0 \
        # Intel GPU 関連のライブラリ
        intel-media-va-driver-non-free intel-opencl-icd libigfxcmrt7 libmfx1 libmfxgen1 libva-drm2 libva-x11-2 ocl-icd-opencl-dev \
        # NVIDIA GPU 関連のライブラリ
        cuda-nvrtc-12-8 libnpp-12-8 \
        # AMD GPU 関連のライブラリ
        amf-amdgpu-pro libamdenc-amdgpu-pro libdrm2-amdgpu ocl-icd-libopencl1 rocm-opencl-runtime vulkan-amdgpu-pro \
        # Zendriver 用に Google Chrome と日本語フォントをインストール
        google-chrome-stable fonts-vlgothic \
        # 【追加】L-SMASH-Works の実行に必要な FFmpeg 共有ライブラリ
        libavcodec58 libavformat58 libavutil56 libswscale5 libswresample3 && \
    # 実行時イメージなので RUN の最後に掃除する
    apt-get -y autoremove && \
    apt-get -y clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/*

# --- メイン環境でも AviSynth+ のみインストール (CUDAFiltersは除外) ---
ENV AVISYNTH_VER=3.7.5
ENV UBUNTU_VERSION=22.04
ENV ARCH=amd64

RUN curl -L -o avisynth.deb https://github.com/rigaya/AviSynthCUDAFilters/releases/download/0.7.3/avisynth_${AVISYNTH_VER}-1_${ARCH}_Ubuntu${UBUNTU_VERSION}.deb \
    && apt-get install -y ./avisynth.deb \
    && rm ./avisynth.deb

# chapter_exe が利用する L-SMASH ライブラリなどのためのパスを通す
ENV LD_LIBRARY_PATH=/usr/local/lib:/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH

# amatsukaze-builder ステージからビルド済みの解析ツールと共有ライブラリ、AviSynthプラグインをコピー
COPY --from=amatsukaze-builder /usr/local/lib/liblsmash* /usr/local/lib/
COPY --from=amatsukaze-builder /usr/local/lib/avisynth/ /usr/local/lib/avisynth/
COPY --from=amatsukaze-builder /usr/local/bin/chapter_exe /usr/local/bin/
COPY --from=amatsukaze-builder /usr/local/bin/join_logo_scp /usr/local/bin/
COPY --from=amatsukaze-builder /usr/local/bin/logoframe /usr/local/bin/

# ダウンロードしておいたサードパーティーライブラリをコピー
WORKDIR /code/server/
COPY --from=thirdparty-downloader /thirdparty/ /code/server/thirdparty/

# Poetry の依存パッケージリストだけをコピー
COPY ./server/pyproject.toml ./server/poetry.lock ./server/poetry.toml /code/server/

# 依存パッケージを poetry でインストール
RUN /code/server/thirdparty/Python/bin/python -m poetry env use /code/server/thirdparty/Python/bin/python && \
    /code/server/thirdparty/Python/bin/python -m poetry install --only main --no-root

# サーバーのソースコードをコピー
COPY ./server/ /code/server/

# クライアントのビルド成果物 (dist) だけをコピー
COPY --from=client-builder /code/client/dist/ /code/client/dist/

# config.example.yaml をコピー
COPY ./config.example.yaml /code/config.example.yaml

# KonomiTV サーバーを起動
ENTRYPOINT ["/code/server/.venv/bin/python", "KonomiTV.py"]
