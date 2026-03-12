import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal, cast

import py7zr
import requests
import ruamel.yaml
from rich import print
from rich.padding import Padding

from Utils import (
    CreateBasicInfiniteProgress,
    CreateDownloadInfiniteProgress,
    CreateDownloadProgress,
    CreateTable,
    CustomPrompt,
    GetNetworkInterfaceInformation,
    IsDockerComposeV2,
    IsDockerInstalled,
    IsGitInstalled,
    RemoveEmojiIfLegacyTerminal,
    RunKonomiTVServiceWaiter,
    RunSubprocess,
    RunSubprocessDirectLogOutput,
    SaveConfig,
    ShowPanel,
    ShowSubProcessErrorLog,
)


def Updater(version: str) -> None:
    """
    KonomiTV のアップデーターの実装

    Args:
        version (str): KonomiTV をアップデートするバージョン
    """

    ShowPanel([
        '[yellow]注意: このアップデーターは現時点では動作しない可能性があります。',
        'KonomiTV は鋭意開発中のため、現在破壊的な構成変更が頻繁に行われています。',
        '破壊的変更が続く中アップデーターの機能を維持することは難しいため、',
        '安定版リリースまでの当面の間、アップデーターは最低限のメンテナンスのみ行っています。',
        'もしアップデーターが動作しない場合は、適宜 DB や設定ファイルなどをバックアップの上で',
        '一旦アンインストールし、新規でインストールし直していただきますようお願いいたします。[/yellow]',
    ])

    # 設定データの対話的な取得とエンコーダーの動作確認を行うかは Installer.py と共通
    # インストール先・アップデート先ディレクトリの決定も Installer.py と共通だが、デフォルトが現在のカレントディレクトリになる

    # プラットフォームタイプ
    ## Windows・Linux・Linux (Docker)
    platform_type: Literal['Windows', 'Linux', 'Linux-Docker'] = 'Windows' if os.name == 'nt' else 'Linux'

    # ARM デバイスかどうか
    is_arm_device = platform.machine() == 'aarch64'

    # ***** KonomiTV をアップデートするフォルダのパス *****

    table_02 = CreateTable()
    table_02.add_column('02. KonomiTV をアップデートするフォルダのパスを入力してください。')
    if platform_type == 'Windows':
        table_02.add_row('例: C:\\DTV\\KonomiTV')
    elif platform_type == 'Linux' or platform_type == 'Linux-Docker':
        table_02.add_row('例: /opt/KonomiTV')
    print(Padding(table_02, (1, 2, 1, 2)))

    # デフォルトの KonomiTV のインストール先のフォルダを取得
    ## バージョンアップなので、基本は現在のカレントディレクトリになる
    update_path_default = Path.cwd().resolve()

    # KonomiTV をアップデートするフォルダのパスを取得
    update_path: Path
    while True:

        # 入力を求める
        update_path_input = CustomPrompt.ask(
            'KonomiTV をアップデートするフォルダ',
            default = str(update_path_default),
        )

        # 指定されたフォルダのパスを Path オブジェクトにする
        try:
            update_path = Path(update_path_input).resolve()
        except ValueError:
            ShowPanel(['[red]指定されたフォルダのパスは不正です。[/red]'])
            continue

        # 指定されたフォルダが存在しない
        if update_path.exists() is False:
            ShowPanel(['[red]指定されたフォルダが存在しません。[/red]'])
            continue

        # 指定されたフォルダに KonomiTV サーバーの実行ファイル (server/KonomiTV.py) がない
        if (update_path / 'server/KonomiTV.py').is_file() is False:
            ShowPanel([
                '[red]指定されたフォルダは KonomiTV のインストール先ではありません。[/red]',
                'KonomiTV をインストールしたフォルダを指定してください。',
            ])
            continue

        break

    # ***** 環境に合ったインストール方法 *****

    if platform_type == 'Windows':

        # 環境にかかわらず常に Windows ネイティブでインストールする
        pass

    elif platform_type == 'Linux' or platform_type == 'Linux-Docker':

        table_03 = CreateTable()
        table_03.add_column('03. KonomiTV のアップデート方法を選択してください。')
        table_03.add_row('Docker を利用したアップデートが最も簡単で、環境を汚さないため推奨されます。')
        table_03.add_row('既に Docker で動作させている場合は、[1] を選択してください。')
        print(Padding(table_03, (1, 2, 1, 2)))

        while True:
            install_method = CustomPrompt.ask(
                'KonomiTV のアップデート方法 [1: Docker, 2: Linux ネイティブ]',
                default = '1',
                choices = ['1', '2'],
                show_choices = False,
            )
            if install_method == '1':
                platform_type = 'Linux-Docker'

                # Docker がインストールされているか確認
                if IsDockerInstalled() is False:
                    ShowPanel([
                        '[red]Docker がインストールされていません。[/red]',
                        'Docker を利用したアップデートを実行するには、事前に Docker のインストールが必要です。',
                    ])
                    continue

            elif install_method == '2':
                platform_type = 'Linux'
            break

    # Docker Compose のコマンドを環境に合わせて設定
    docker_compose_command = ['docker', 'compose'] if IsDockerComposeV2() else ['docker-compose']

    # サーバーのポート番号を取得
    server_port = 7000
    try:
        if (update_path / 'config.yaml').exists():
            yaml = ruamel.yaml.YAML()
            config_dict = cast(dict[str, Any], yaml.load(update_path / 'config.yaml'))
            server_port = config_dict['server']['port']
    except Exception:
        pass


    # ***** サービスの停止 *****

    if platform_type == 'Windows':

        # 既存のタスクスケジューラのタスクを削除 (もしあれば)
        print(Padding('KonomiTV のタスクスケジューラのタスクを削除しています…', (1, 2, 0, 2)))
        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            subprocess.run(
                args = ['schtasks', '/Delete', '/TN', 'KonomiTV', '/F'],
                stdout = subprocess.DEVNULL,
                stderr = subprocess.DEVNULL,
            )

        # バックグラウンドで実行されている KonomiTV サービス (pm2) を停止
        print(Padding('KonomiTV サービスを停止しています…', (1, 2, 0, 2)))
        result = RunSubprocess(
            name = 'KonomiTV サービスの停止',
            args = [str(update_path / 'thirdparty/Node.js/npm.cmd'), 'run', 'pm2', '--', 'delete', 'KonomiTV'],
            cwd = update_path,  # カレントディレクトリを KonomiTV のインストールフォルダに設定
            error_message = 'KonomiTV サービスの停止中に予期しないエラーが発生しました。',
            error_log_name = 'pm2 のエラーログ',
        )
        if result is False:
            # サービスが実行されていないだけかもしれないので、エラーになっても無視する
            pass

        # 念のため、現在実行中の python.exe を強制終了
        ## python.exe のほか、KonomiTV に同梱の実行ファイルも対象
        print(Padding('KonomiTV のプロセスを終了しています…', (1, 2, 0, 2)))
        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            subprocess.run(
                args = ['taskkill', '/F', '/IM', 'python.exe', '/IM', 'akebi-https-server.exe', '/IM', 'FFmpeg.exe', '/IM', 'FFprobe.exe', '/IM', 'chapter_exe.exe', '/IM', 'join_logo_scp.exe'],
                stdout = subprocess.DEVNULL,
                stderr = subprocess.DEVNULL,
            )

    elif platform_type == 'Linux':

        # バックグラウンドで実行されている KonomiTV サービス (systemd) を停止
        print(Padding('KonomiTV サービスを停止しています…', (1, 2, 0, 2)))
        result = RunSubprocess(
            name = 'KonomiTV サービスの停止',
            args = ['systemctl', 'stop', 'konomitv.service'],
            error_message = 'KonomiTV サービスの停止中に予期しないエラーが発生しました。',
            error_log_name = 'systemctl のエラーログ',
        )
        if result is False:
            # サービスが実行されていないだけかもしれないので、エラーになっても無視する
            pass

    elif platform_type == 'Linux-Docker':

        # コンテナの停止と削除
        print(Padding('KonomiTV サービスを停止しています…', (1, 2, 0, 2)))
        result = RunSubprocess(
            name = 'Docker コンテナの停止と削除',
            args = [*docker_compose_command, 'down'],
            cwd = update_path,  # カレントディレクトリを KonomiTV のインストールフォルダに設定
            error_message = 'Docker コンテナの停止中に予期しないエラーが発生しました。',
            error_log_name = 'Docker Compose のエラーログ',
        )
        if result is False:
            return  # 処理中断


    # ***** サードパーティーライブラリのダウンロードと配置 *****

    # ダウンロードする URL
    ## TODO: 暫定的に v0.13.0 のリリースを使用している
    if platform_type == 'Windows':
        download_url = f'https://github.com/tsukumijima/KonomiTV/releases/download/v0.13.0/thirdparty-windows.7z'
    elif platform_type == 'Linux':
        download_url = f'https://github.com/tsukumijima/KonomiTV/releases/download/v0.13.0/thirdparty-linux.tar.xz'

    print(Padding('サードパーティーライブラリをダウンロードしています…', (1, 2, 0, 2)))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        download_path = temp_dir_path / 'thirdparty.archive'

        # サードパーティーライブラリをダウンロード
        try:
            with CreateDownloadProgress() as progress:
                task = progress.add_task('Downloading...', total=0)
                response = requests.get(download_url, stream=True)
                response.raise_for_status()
                total_size = int(response.headers.get('Content-Length', 0))
                progress.update(task, total=total_size)
                with open(download_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
                        progress.update(task, advance=len(chunk))
        except Exception as ex:
            ShowPanel([
                '[red]サードパーティーライブラリのダウンロード中にエラーが発生しました。[/red]',
                'お手数をおかけしますが、下記のログを開発者に報告してください。',
            ])
            print(ex)
            return

        # サードパーティーライブラリを解凍する
        print(Padding('サードパーティーライブラリを解凍しています… (これには数分かかります)', (1, 2, 0, 2)))
        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            if platform_type == 'Windows':
                with py7zr.SevenZipFile(download_path, mode='r') as zip_file:
                    zip_file.extractall(path=temp_dir_path)
            elif platform_type == 'Linux':
                with tarfile.open(download_path, 'r:xz') as tar_file:
                    tar_file.extractall(path=temp_dir_path)

        # サードパーティーライブラリを配置する
        print(Padding('サードパーティーライブラリを配置しています…', (1, 2, 0, 2)))
        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            shutil.rmtree(update_path / 'thirdparty', ignore_errors=True)
            if platform_type == 'Windows':
                shutil.move(temp_dir_path / 'thirdparty-windows/thirdparty', update_path / 'thirdparty')
            elif platform_type == 'Linux':
                shutil.move(temp_dir_path / 'thirdparty', update_path / 'thirdparty')

    # ***** KonomiTV 内蔵の Amatsukaze ツールのダウンロード・ビルドと配置 *****
    if platform_type == 'Windows':
        print(Padding('CM解析ツール (Amatsukaze) をダウンロードしています…', (1, 2, 0, 2)))
        try:
            # Amatsukaze の公式ビルド済み ZIP の URL
            amatsukaze_url = 'https://github.com/nekopanda/Amatsukaze/releases/download/0.9.5.8/Amatsukaze_0.9.5.8.zip'
            with tempfile.TemporaryDirectory() as am_temp_dir:
                am_temp_dir_path = Path(am_temp_dir)
                amatsukaze_zip_path = am_temp_dir_path / 'Amatsukaze.zip'
                
                with CreateDownloadProgress() as progress:
                    task = progress.add_task('Downloading...', total=0)
                    response = requests.get(amatsukaze_url, stream=True)
                    response.raise_for_status()
                    total_size = int(response.headers.get('Content-Length', 0))
                    progress.update(task, total=total_size)
                    with open(amatsukaze_zip_path, 'wb') as file:
                        for chunk in response.iter_content(chunk_size=8192):
                            file.write(chunk)
                            progress.update(task, advance=len(chunk))
                
                print(Padding('CM解析ツール (Amatsukaze) を配置しています…', (1, 2, 0, 2)))
                progress_extract = CreateBasicInfiniteProgress()
                progress_extract.add_task('', total=None)
                with progress_extract:
                    amatsukaze_bin_dir = update_path / 'thirdparty' / 'Amatsukaze' / 'bin'
                    amatsukaze_lib_dir = update_path / 'thirdparty' / 'Amatsukaze' / 'lib' / 'avisynth'
                    amatsukaze_bin_dir.mkdir(parents=True, exist_ok=True)
                    amatsukaze_lib_dir.mkdir(parents=True, exist_ok=True)
                    
                    with zipfile.ZipFile(amatsukaze_zip_path, 'r') as zip_ref:
                        for item in zip_ref.namelist():
                            if item.endswith('/'):
                                continue
                            filename = os.path.basename(item)
                            
                            # CM解析用のメイン実行ファイル
                            if filename in ['chapter_exe.exe', 'join_logo_scp.exe', 'logoframe.exe']:
                                source = zip_ref.open(item)
                                with open(amatsukaze_bin_dir / filename, "wb") as target:
                                    shutil.copyfileobj(source, target)
                            # AviSynth プラグイン (L-SMASH Works)
                            elif filename == 'LSMASHSource.dll':
                                source = zip_ref.open(item)
                                with open(amatsukaze_lib_dir / filename, "wb") as target:
                                    shutil.copyfileobj(source, target)
                            # その他の依存 DLL (ポータブル動作のための avisynth.dll 等も含む)
                            elif filename.endswith('.dll'):
                                source = zip_ref.open(item)
                                with open(amatsukaze_bin_dir / filename, "wb") as target:
                                    shutil.copyfileobj(source, target)
        except Exception as ex:
            ShowPanel([
                '[yellow]CM解析ツール (Amatsukaze) のダウンロードに失敗しました。[/yellow]',
                'CM自動スキップ機能は動作しませんが、KonomiTV のアップデート自体は継続します。',
            ])
            print(ex)

    elif platform_type == 'Linux' and is_arm_device is False:
        print(Padding('CM解析ツール (Amatsukaze) の Linux ネイティブビルドを行っています… (数分〜数十分かかります)', (1, 2, 0, 2)))
        try:
            with tempfile.TemporaryDirectory() as am_temp_dir:
                am_temp_dir_path = Path(am_temp_dir)
                
                # 依存パッケージのインストールとビルドをシェルスクリプトにまとめる
                build_script = """#!/bin/bash
set -ex

export DEBIAN_FRONTEND=noninteractive

# 依存パッケージのインストール
apt-get update
apt-get install -y build-essential git curl wget p7zip-full nasm cmake meson ninja-build pkg-config autoconf automake libtool openssl zlib1g libz-dev libssl-dev libavformat-dev libavcodec-dev libswscale-dev libavutil-dev libswresample-dev

# AviSynth+
curl -L -o avisynth.deb https://github.com/rigaya/AviSynthCUDAFilters/releases/download/0.7.3/avisynth_3.7.5-1_amd64_Ubuntu22.04.deb
apt-get install -y ./avisynth.deb

# L-SMASH
git clone https://github.com/l-smash/l-smash.git
cd l-smash
git checkout 18a9ed25c7ff79a7f4f4bf850c345c72179b8998
./configure --enable-shared
make -j$(nproc)
make install
cd ..

# L-SMASH-Works
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH
git clone https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works.git
cd L-SMASH-Works
git checkout $(git rev-list -n 1 --before="2021-11-01" HEAD)
cd AviSynth
if [ -f meson.build ]; then
    meson setup build && ninja -C build && cp build/liblsmashsource.so LSMASHSource.so
else
    ./configure && make -j$(nproc) && cp LSMASHSource.so LSMASHSource.so
fi
cd ../..

# chapter_exe
git clone https://github.com/rigaya/chapter_exe
cd chapter_exe
git checkout 32880d45f088e574285a101e6a49b032bb04f6ea
cd src
make -j$(nproc)
cd ../..

# join_logo_scp
git clone --depth=1 --branch Ver4.1.0_Linux https://github.com/tobitti0/join_logo_scp
cd join_logo_scp/src
make -j$(nproc)
cd ../..

# logoframe
git clone --recursive https://github.com/tobitti0/JoinLogoScpTrialSetLinux.git
cd JoinLogoScpTrialSetLinux/modules/logoframe/src
make -j$(nproc)
cd ../../..
"""
                build_script_path = am_temp_dir_path / 'build.sh'
                build_script_path.write_text(build_script, encoding='utf-8')
                os.chmod(build_script_path, 0o755)

                result = RunSubprocess(
                    name = 'CM解析ツールのソースからのビルド',
                    args = ['bash', str(build_script_path)],
                    cwd = am_temp_dir_path,
                    error_message = 'CM解析ツールのビルド中にエラーが発生しました。',
                    error_log_name = 'ビルドのエラーログ',
                )

                if result is False:
                    ShowPanel([
                        '[yellow]CM解析ツール (Amatsukaze) のビルドに失敗しました。[/yellow]',
                        'CM自動スキップ機能は動作しませんが、KonomiTV のアップデート自体は継続します。',
                    ])
                else:
                    print(Padding('ビルドした CM解析ツール を配置しています…', (1, 2, 0, 2)))
                    amatsukaze_bin_dir = update_path / 'thirdparty' / 'Amatsukaze' / 'bin'
                    amatsukaze_lib_dir = update_path / 'thirdparty' / 'Amatsukaze' / 'lib' / 'avisynth'
                    amatsukaze_bin_dir.mkdir(parents=True, exist_ok=True)
                    amatsukaze_lib_dir.mkdir(parents=True, exist_ok=True)
                    
                    shutil.copy(am_temp_dir_path / 'chapter_exe/src/chapter_exe', amatsukaze_bin_dir / 'chapter_exe.elf')
                    shutil.copy(am_temp_dir_path / 'join_logo_scp/src/join_logo_scp', amatsukaze_bin_dir / 'join_logo_scp.elf')
                    shutil.copy(am_temp_dir_path / 'JoinLogoScpTrialSetLinux/modules/logoframe/src/logoframe', amatsukaze_bin_dir / 'logoframe.elf')
                    
                    for elf_file in amatsukaze_bin_dir.glob('*.elf'):
                        os.chmod(elf_file, 0o755)

                    shutil.copy(am_temp_dir_path / 'L-SMASH-Works/AviSynth/LSMASHSource.so', amatsukaze_lib_dir / 'LSMASHSource.so')
                    
                    for so_file in Path('/usr/local/lib').glob('liblsmash.so*'):
                        if so_file.is_file() or so_file.is_symlink():
                            shutil.copy2(so_file, amatsukaze_lib_dir / so_file.name)
                            
        except Exception as ex:
            ShowPanel([
                '[yellow]CM解析ツール (Amatsukaze) のビルド処理中に予期しないエラーが発生しました。[/yellow]',
                'CM自動スキップ機能は動作しませんが、KonomiTV のアップデート自体は継続します。',
            ])
            print(ex)

    elif platform_type == 'Linux' and is_arm_device is True:
        print(Padding('ARM 環境のため、CM解析ツール (Amatsukaze) のビルドはスキップされます。', (1, 2, 0, 2)))
        ShowPanel([
            '[yellow]注意: お使いの環境 (ARM アーキテクチャ) では、CM自動スキップ機能は利用できません。[/yellow]',
            'CM解析に利用しているツール群 (Amatsukaze) が ARM アーキテクチャに対応していないため、',
            'ツールのビルドと配置はスキップされます。KonomiTV のアップデート自体は継続します。',
        ])


    # ***** KonomiTV サーバーのアップグレード *****

    print(Padding('KonomiTV サーバーをアップデートしています…', (1, 2, 0, 2)))

    # リポジトリのアップデート
    if IsGitInstalled():

        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            RunSubprocessDirectLogOutput(['git', 'fetch', 'origin'], cwd=update_path)
            RunSubprocessDirectLogOutput(['git', 'reset', '--hard', 'origin/master'], cwd=update_path)

    # .zip からのアップデート (Git がインストールされていない環境など)
    else:

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            download_path = temp_dir_path / 'KonomiTV.zip'

            # GitHub からアップデート対象のバージョンの .zip をダウンロード
            try:
                with CreateDownloadProgress() as progress:
                    task = progress.add_task('Downloading...', total=0)
                    response = requests.get(f'https://github.com/tsukumijima/KonomiTV/archive/refs/tags/{version}.zip', stream=True)
                    response.raise_for_status()
                    total_size = int(response.headers.get('Content-Length', 0))
                    progress.update(task, total=total_size)
                    with open(download_path, 'wb') as file:
                        for chunk in response.iter_content(chunk_size=8192):
                            file.write(chunk)
                            progress.update(task, advance=len(chunk))
            except Exception as ex:
                ShowPanel([
                    '[red]KonomiTV サーバーのアップデート用ソースコードのダウンロード中にエラーが発生しました。[/red]',
                    'お手数をおかけしますが、下記のログを開発者に報告してください。',
                ])
                print(ex)
                return

            # ダウンロードした .zip を解凍して配置する
            progress = CreateBasicInfiniteProgress()
            progress.add_task('', total=None)
            with progress:
                with zipfile.ZipFile(download_path, mode='r') as zip_file:
                    zip_file.extractall(path=temp_dir_path)

                # KonomiTV のフォルダ内の既存のファイルやディレクトリを KonomiTV-master 内のものと入れ替える
                ## config.yaml, data/, logs/, thirdparty/ は保持し、それ以外のアップデート対象外のファイルは消去する
                for update_path_child in update_path.iterdir():
                    if update_path_child.name not in ['config.yaml', 'data', 'logs', 'thirdparty']:
                        if update_path_child.is_file() or update_path_child.is_symlink():
                            update_path_child.unlink()
                        elif update_path_child.is_dir():
                            shutil.rmtree(update_path_child, ignore_errors=True)

                # KonomiTV-master フォルダ内のファイルを KonomiTV のインストールフォルダに移動する
                update_src_path = temp_dir_path / f'KonomiTV-{version.replace("v", "")}'
                for update_src_child in update_src_path.iterdir():
                    if update_src_child.name not in ['config.yaml', 'data', 'logs', 'thirdparty']:
                        shutil.move(update_src_child, update_path / update_src_child.name)


    # ***** 依存パッケージのインストール *****

    if platform_type == 'Windows' or platform_type == 'Linux':

        # サーバー / クライアント共にビルド・インストール済みの KonomiTV リリース版 (.zip) を利用しているとみなして、
        # Poetry や npm での依存パッケージのインストール / クライアントのビルドをスキップする
        pass

    elif platform_type == 'Linux-Docker':

        # Docker イメージをビルド
        ## KonomiTV の Docker イメージはビルド済みで GitHub Container Registry にアップロードされているため、
        ## docker-compose.yaml 内で build: ではなく image: ghcr.io/tsukumijima/konomitv:latest が指定されている場合は
        ## ビルドされず、プルだけが行われる
        print(Padding('Docker イメージをビルド・プルしています…', (1, 2, 0, 2)))
        result = RunSubprocess(
            name = 'Docker イメージのビルド・プル',
            args = [*docker_compose_command, 'build', '--pull'],
            cwd = update_path,  # カレントディレクトリを KonomiTV のインストールフォルダに設定
            error_message = 'Docker イメージのビルド・プル中に予期しないエラーが発生しました。',
            error_log_name = 'Docker Compose のエラーログ',
        )
        if result is False:
            return  # 処理中断


    # ***** サービスの開始 *****

    if platform_type == 'Windows':

        # config.yaml から KonomiTV のポート番号を取得し、設定されていれば上書きする
        # ここで設定したポート番号で、受信規則の開放が行われる
        server_port = 7000
        try:
            if (update_path / 'config.yaml').exists():
                yaml = ruamel.yaml.YAML()
                config_dict = cast(dict[str, Any], yaml.load(update_path / 'config.yaml'))
                server_port = config_dict['server']['port']
        except Exception:
            pass

        # Windows Defender ファイアウォールに KonomiTV サーバーのポートを開放する受信規則を追加
        # すでに存在するかもしれないので、エラーになっても無視する
        print(Padding('Windows Defender ファイアウォールに受信規則を追加しています…', (1, 2, 0, 2)))
        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            subprocess.run(
                args = [
                    'netsh', 'advfirewall', 'firewall', 'add', 'rule',
                    'name=KonomiTV Service',
                    'dir=in',
                    'action=allow',
                    'protocol=TCP',
                    f'localport={server_port}',
                ],
                stdout = subprocess.DEVNULL,  # 標準出力を表示しない
                stderr = subprocess.DEVNULL,  # 標準エラー出力を表示しない
            )

        # バックグラウンドで実行される KonomiTV サービス (pm2) を開始
        print(Padding('KonomiTV サービスを開始しています…', (1, 2, 0, 2)))
        result = RunSubprocess(
            name = 'KonomiTV サービスの開始',
            args = [str(update_path / 'thirdparty/Node.js/npm.cmd'), 'run', 'pm2', '--', 'start', 'server/KonomiTV.py', '--name', 'KonomiTV', '--interpreter', 'thirdparty/Python/python.exe'],
            cwd = update_path,  # カレントディレクトリを KonomiTV のインストールフォルダに設定
            error_message = 'KonomiTV サービスの開始中に予期しないエラーが発生しました。',
            error_log_name = 'pm2 のエラーログ',
        )
        if result is False:
            return  # 処理中断

        # pm2 の設定を保存 (タスクスケジューラからの自動起動で復元される)
        print(Padding('KonomiTV サービスの設定を保存しています…', (1, 2, 0, 2)))
        result = RunSubprocess(
            name = 'KonomiTV サービスの設定の保存',
            args = [str(update_path / 'thirdparty/Node.js/npm.cmd'), 'run', 'pm2', '--', 'save'],
            cwd = update_path,  # カレントディレクトリを KonomiTV のインストールフォルダに設定
            error_message = 'KonomiTV サービスの設定の保存中に予期しないエラーが発生しました。',
            error_log_name = 'pm2 のエラーログ',
        )
        if result is False:
            return  # 処理中断

        # pm2 のタスクスケジューラからの自動起動スクリプトを作成
        print(Padding('タスクスケジューラに自動起動タスクを追加しています…', (1, 2, 0, 2)))
        script_path = update_path / 'KonomiTV-Service.bat'
        with open(script_path, 'w') as file:
            file.write(
                f'@echo off\n'
                f'cd /d "{update_path}"\n'
                f'set PM2_HOME={update_path / ".pm2"}\n'
                f'thirdparty\\Node.js\\npm.cmd run pm2 -- resurrect\n'
            )

        # Windows のタスクスケジューラにユーザーのログオン時に実行するタスクを追加
        ## VBScript を経由し、ウィンドウを非表示にして実行させる (VBScript は installer.py と同じフォルダにある前提)
        vbs_path = Path(__file__).resolve().parent / 'KonomiTV-Service.vbs'
        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            subprocess.run(
                args = [
                    'schtasks', '/Create', '/TN', 'KonomiTV', '/TR', f'wscript.exe "{vbs_path}" "{script_path}"', '/SC', 'ONLOGON', '/RL', 'HIGHEST', '/F'
                ],
                stdout = subprocess.DEVNULL,  # 標準出力を表示しない
                stderr = subprocess.DEVNULL,  # 標準エラー出力を表示しない
            )

    elif platform_type == 'Linux':

        # systemd に KonomiTV サービス (konomitv.service) を登録
        print(Padding('systemd に KonomiTV サービスを追加しています…', (1, 2, 0, 2)))
        service_path = update_path / 'konomitv.service'
        with open(service_path, 'w') as file:
            file.write(
                f'[Unit]\n'
                f'Description=KonomiTV Service\n'
                f'After=network.target\n'
                f'\n'
                f'[Service]\n'
                f'Type=simple\n'
                f'WorkingDirectory={update_path}\n'
                f'ExecStart={update_path / "thirdparty/Python/bin/python"} {update_path / "server/KonomiTV.py"}\n'
                f'Restart=always\n'
                f'RestartSec=3\n'
                f'\n'
                f'[Install]\n'
                f'WantedBy=multi-user.target\n'
            )

        # systemctl を使ってサービスを有効化・起動
        print(Padding('KonomiTV サービスを開始しています…', (1, 2, 0, 2)))
        progress = CreateBasicInfiniteProgress()
        progress.add_task('', total=None)
        with progress:
            subprocess.run(['systemctl', 'enable', str(service_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['systemctl', 'daemon-reload'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = RunSubprocess(
            name = 'KonomiTV サービスの開始',
            args = ['systemctl', 'restart', 'konomitv.service'],
            error_message = 'KonomiTV サービスの開始中に予期しないエラーが発生しました。',
            error_log_name = 'systemctl のエラーログ',
        )
        if result is False:
            return  # 処理中断

    elif platform_type == 'Linux-Docker':

        # コンテナの起動
        print(Padding('Docker コンテナを起動しています…', (1, 2, 0, 2)))
        result = RunSubprocess(
            name = 'Docker コンテナの起動',
            args = [*docker_compose_command, 'up', '-d', '--force-recreate'],
            cwd = update_path,  # カレントディレクトリを KonomiTV のインストールフォルダに設定
            error_message = 'Docker コンテナの起動中に予期しないエラーが発生しました。',
            error_log_name = 'Docker Compose のエラーログ',
        )
        if result is False:
            return  # 処理中断

    # ***** サービスの起動を待機 *****

    # KonomiTV サービスの起動を監視して起動完了を待機する処理はインストーラーと共通
    RunKonomiTVServiceWaiter(platform_type, update_path)

    # ***** アップデート完了 *****

    # ループバックアドレスまたはリンクローカルアドレスでない IPv4 アドレスとインターフェイス名を取得
    nic_infos = GetNetworkInterfaceInformation()

    # アップデート完了メッセージを表示
    table_done = CreateTable()
    table_done.add_column(RemoveEmojiIfLegacyTerminal(
        'アップデートが完了しました！🎉🎊 すぐに使いはじめられます！🎈\n'
        '下記の URL から、KonomiTV の Web UI にアクセスしてみましょう！\n'
        'もし KonomiTV にアクセスできない場合は、ファイアウォールの設定を確認してみてください。'
    ))

    # アクセス可能な URL のリストを IP アドレスごとに表示
    ## ローカルホスト (127.0.0.1) だけは https://my.local.konomi.tv:7000/ というエイリアスが使える
    urls = [f'https://{nic_info[0].replace(".", "-")}.local.konomi.tv:{server_port}/' for nic_info in nic_infos]
    if '127.0.0.1' in [nic_info[0] for nic_info in nic_infos]:
        urls.append(f'https://my.local.konomi.tv:{server_port}/')
    table_done.add_row('\n'.join(urls))

    print(Padding(table_done, (1, 2, 1, 2)))

    # Windows でのみ完了後 15 秒間待機し、自動で閉じる
    # Linux では自動で閉じられると困るケースの方が多いのでそのまま
    if os.name == 'nt':
        import time
        from rich.live import Live
        with Live(transient=True) as live:
            for i in range(15, 0, -1):
                live.update(Padding(f'{i} 秒後に自動で終了します…', (0, 2, 0, 2)))
                time.sleep(1)
