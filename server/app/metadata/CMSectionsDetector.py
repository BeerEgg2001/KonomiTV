from __future__ import annotations

import asyncio
import glob
import os
import pathlib
import tempfile
import time

import anyio
import typer

from app import logging, schemas
from app.config import LoadConfig
from app.constants import BASE_DIR
from app.models.RecordedVideo import RecordedVideo


class CMSectionsDetector:
    """
    録画 TS ファイルに含まれる CM 区間を検出するクラス
    録画ファイルと同じファイル名で .chapter.txt が保存されていればそこから CM 区間情報を取得し、
    .chapter.txt が存在しない場合は自前で CM 区間を検出し、.chapter.txt を生成してから取得する
    """

    def __init__(self, file_path: anyio.Path, duration_sec: float) -> None:
        """
        録画 TS ファイルに含まれる CM 区間を検出するクラスを初期化する

        Args:
            file_path (anyio.Path): 動画ファイルのパス
            duration_sec (float): 動画の再生時間(秒)
        """

        self.file_path = file_path
        self.duration_sec = duration_sec


    async def detectAndSave(self) -> None:
        """
        録画ファイルの CM 区間を検出し、データベースに保存する
        """

        start_time = time.time()
        logging.info(f'{self.file_path}: Detecting CM sections...')
        try:
            # 1. 録画ファイルに対応するチャプターファイル (.chapter.txt) の読み込みを試みる
            cm_sections = await self.__detectFromChapterFile()

            # 2. チャプターファイルが存在しない場合は、自前で解析して .chapter.txt を生成する
            if not cm_sections and not await self.__chapterFileExists():
                success = await self.__generateChapterFile()
                if success:
                    # 生成した .chapter.txt を既存のロジックで読み込む
                    cm_sections = await self.__detectFromChapterFile()

            # 解析・生成に失敗した場合、またはCMが1つもなかった場合は []
            if cm_sections is None:
                cm_sections = []

            for cm_section in cm_sections:
                logging.debug(f'{self.file_path}: CM section detected: {cm_section["start_time"]} - {cm_section["end_time"]}')

            # 3. 検出結果をデータベースに保存
            db_recorded_video = await RecordedVideo.get_or_none(file_path=str(self.file_path))
            if db_recorded_video is not None:
                db_recorded_video.cm_sections = cm_sections
                await db_recorded_video.save()
                if len(cm_sections) > 0:
                    logging.info(f'{self.file_path}: Saved {len(cm_sections)} CM sections. ({time.time() - start_time:.2f} sec)')
                else:
                    logging.info(f'{self.file_path}: No CM sections detected. ({time.time() - start_time:.2f} sec)')
            else:
                logging.warning(f'{self.file_path}: RecordedVideo record not found.')

        except Exception as ex:
            logging.error(f'{self.file_path}: Error saving CM sections to DB:', exc_info=ex)


    async def __chapterFileExists(self) -> bool:
        """チャプターファイルが存在するか確認する"""
        chapter_file_path = self.file_path.with_name(f"{self.file_path.stem}.chapter.txt")
        return await chapter_file_path.exists()


    async def __generateChapterFile(self) -> bool:
        """
        join_logo_scp (with chapter_exe) を使ってCM解析を行い、
        結果をNero Chapter形式で .chapter.txt として録画フォルダに保存する
        
        Returns:
            bool: ファイルの生成に成功したかどうか
        """

        chapter_exe_path = "/usr/local/bin/chapter_exe"
        logoframe_path = "/usr/local/bin/logoframe"
        join_logo_scp_path = "/usr/local/bin/join_logo_scp"

        for tool in [chapter_exe_path, join_logo_scp_path]:
            if not os.path.exists(tool):
                logging.error(f'{self.file_path}: {tool} is not found. Cannot detect CM sections.')
                return False

        db_recorded_video = await RecordedVideo.get_or_none(file_path=str(self.file_path)).prefetch_related(
            "recorded_program", "recorded_program__channel"
        )
        
        sid = None
        if db_recorded_video and db_recorded_video.recorded_program and db_recorded_video.recorded_program.channel:
            sid = db_recorded_video.recorded_program.channel.service_id

        logo_path = None
        if sid is not None:
            logo_dir = BASE_DIR / "logo"
            if logo_dir.exists():
                search_pattern = str(logo_dir / f"SID{sid}-*.lgd")
                logo_files = glob.glob(search_pattern)
                if logo_files:
                    logo_path = pathlib.Path(logo_files[0])
                    logging.info(f'{self.file_path}: Found logo file for SID {sid}: {logo_path.name}')
                else:
                    logging.info(f'{self.file_path}: Logo file for SID {sid} not found. Proceeding with fallback (chapter_exe only) analysis.')
            else:
                logging.warning(f'{self.file_path}: Logo directory {logo_dir} does not exist.')

        with tempfile.TemporaryDirectory(prefix="konomitv_cm_") as temp_dir:
            temp_dir_path = pathlib.Path(temp_dir)
            
            # 録画ディレクトリの汚染防止（.lwi ファイル対策）
            temp_ts_file = temp_dir_path / self.file_path.name
            os.symlink(str(self.file_path), str(temp_ts_file))

            # FFmpegによる音声の事前分離 (最速化)
            wav_file = temp_dir_path / "audio.wav"
            ffmpeg_path = BASE_DIR / "thirdparty" / "FFmpeg" / "ffmpeg.elf"
            ffmpeg_cmd = str(ffmpeg_path) if ffmpeg_path.exists() else "ffmpeg"

            env = os.environ.copy()
            if ffmpeg_path.exists():
                env["LD_LIBRARY_PATH"] = f"{str(BASE_DIR / 'thirdparty' / 'FFmpeg')}:{env.get('LD_LIBRARY_PATH', '')}"

            logging.info(f'{self.file_path}: Extracting audio using ffmpeg for acceleration...')
            try:
                ffmpeg_proc = await asyncio.create_subprocess_exec(
                    ffmpeg_cmd, "-y", "-i", str(self.file_path),
                    "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
                    str(wav_file),
                    env=env,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await ffmpeg_proc.communicate()
            except Exception as e:
                logging.warning(f'{self.file_path}: Audio extraction failed. Fallback to Avisynth internal audio: {e}')
                wav_file = None

            # AviSynthスクリプト(.avs)の動的生成
            avs_file = temp_dir_path / "input.avs"
            if wav_file and wav_file.exists():
                avs_content = (
                    f'LoadPlugin("/usr/local/lib/avisynth/LSMASHSource.so")\n'
                    f'LWLibavVideoSource("{temp_ts_file}")\n'
                )
            else:
                avs_content = (
                    f'LoadPlugin("/usr/local/lib/avisynth/LSMASHSource.so")\n'
                    f'LWLibavVideoSource("{temp_ts_file}")\n'
                    f'AudioDub(last, LWLibavAudioSource("{temp_ts_file}"))\n'
                )
            avs_file.write_text(avs_content, encoding='utf-8')

            in_file = temp_dir_path / "chapter_exe.in"
            logo_txt_file = temp_dir_path / "logo.logo.txt"
            jls_out_file = temp_dir_path / "jls.txt"

            try:
                # [Step 1] chapter_exe
                logging.info(f'{self.file_path}: Running chapter_exe...')
                cmd_chapter = [chapter_exe_path, "-v", str(avs_file), "-o", str(in_file)]
                if wav_file and wav_file.exists():
                    cmd_chapter.extend(["-a", str(wav_file)])

                process = await asyncio.create_subprocess_exec(
                    *cmd_chapter,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()

                if process.returncode != 0 or not in_file.exists():
                    logging.error(f'{self.file_path}: chapter_exe exited with code {process.returncode} or output file not found.')
                    return False
                
                logging.info(f'{self.file_path}: chapter_exe analysis completed.')

                # ロゴがない場合はフォールバック処理で .chapter.txt を生成
                if not logo_path or not logo_path.exists():
                    logging.info(f'{self.file_path}: Executing fallback CM detection using chapter_exe results.')
                    return await self.__generate_fallback_chapter_file(in_file, fps=30000/1001)

                # [Step 2] logoframe
                has_logo_txt = False
                logging.info(f'{self.file_path}: Running logoframe...')
                cmd_logoframe = [logoframe_path, str(avs_file), "-logo", str(logo_path), "-oa", str(logo_txt_file)]
                process = await asyncio.create_subprocess_exec(
                    *cmd_logoframe,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()
                
                if process.returncode == 0 and logo_txt_file.exists():
                    has_logo_txt = True
                    logging.info(f'{self.file_path}: logoframe analysis completed.')
                else:
                    logging.warning(f'{self.file_path}: logoframe failed or logo txt not created.')

                # [Step 3] join_logo_scp
                logging.info(f'{self.file_path}: Running join_logo_scp...')
                jl_cmd_file = BASE_DIR / "data" / "JL" / "JL_標準.txt"
                if not jl_cmd_file.exists():
                    logging.error(f'{self.file_path}: Command file not found: {jl_cmd_file}')
                    return False

                cmd_jls = [
                    join_logo_scp_path,
                    "-inscp", str(in_file),
                    "-incmd", str(jl_cmd_file),
                    "-o", str(temp_dir_path / "output.avs"),
                    "-oscp", str(jls_out_file)
                ]
                if has_logo_txt:
                    cmd_jls.extend(["-inlogo", str(logo_txt_file)])

                process = await asyncio.create_subprocess_exec(
                    *cmd_jls,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()
                
                if not jls_out_file.exists():
                    logging.error(f'{self.file_path}: join_logo_scp failed to create JLS output file.')
                    return False
                    
                logging.info(f'{self.file_path}: join_logo_scp analysis completed.')

                # [Step 4] JLS出力結果から Nero Chapter 形式のテキストを構築して保存
                try:
                    lines = jls_out_file.read_text(encoding='utf-8', errors='ignore').splitlines()
                except Exception as ex:
                    logging.error(f'{self.file_path}: Failed to read generated JLS output file:', exc_info=ex)
                    return False

                chapters_text = []
                chapter_num = 1
                current_type = None
                fps = 30000 / 1001

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    
                    if len(parts) >= 6:
                        try:
                            start_frame = int(parts[0])
                            attr = parts[-1]
                            is_cm = attr.endswith('CM')
                            # 属性が変わったタイミングでチャプターを打つ
                            block_type = 'CM' if is_cm else '本編'
                            
                            if current_type != block_type:
                                time_sec = start_frame / fps
                                time_str = self.__format_time(time_sec)
                                chapters_text.append(f"CHAPTER{chapter_num:02d}={time_str}")
                                chapters_text.append(f"CHAPTER{chapter_num:02d}NAME={block_type}")
                                chapter_num += 1
                                current_type = block_type
                        except ValueError:
                            continue

                if chapters_text:
                    chapter_file_path = self.file_path.with_name(f"{self.file_path.stem}.chapter.txt")
                    # ★修正：await を追加
                    await chapter_file_path.write_text('\n'.join(chapters_text), encoding='utf-8')
                    logging.info(f'{self.file_path}: Successfully generated .chapter.txt from join_logo_scp.')
                    return True
                
                return False

            except Exception as ex:
                logging.error(f'{self.file_path}: Error running CM detection tools:', exc_info=ex)
                return False


    async def __generate_fallback_chapter_file(self, chapter_in_file: pathlib.Path, fps: float) -> bool:
        """
        chapter_exeの出力結果から簡易的にCM区間を推測し、Nero Chapter形式で保存する
        """
        try:
            lines = chapter_in_file.read_text(encoding='utf-8', errors='ignore').splitlines()
            sc_times = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Nero Chapter 形式
                if line.startswith('CHAPTER') and 'NAME' not in line and '=' in line:
                    try:
                        sc_times.append(self.__timeToSeconds(line.split('=')[1]))
                    except Exception:
                        pass
                # CSVフレーム形式
                elif ',' in line:
                    parts = line.split(',')
                    if len(parts) >= 2 and parts[1].strip() == '1':
                        try:
                            sc_times.append(int(parts[0]) / fps)
                        except ValueError:
                            pass
            
            if not sc_times:
                return False

            sc_times = sorted(list(set(sc_times)))
            
            def is_cm_duration(duration: float) -> bool:
                remainder = duration % 15.0
                return remainder <= 1.5 or remainder >= 13.5
                
            cm_blocks = []
            i = 0
            while i < len(sc_times) - 1:
                found_block = False
                for j in range(i + 1, min(i + 15, len(sc_times))):
                    diff = sc_times[j] - sc_times[i]
                    if diff > 121.5:
                        break
                    if diff >= 13.5 and is_cm_duration(diff):
                        cm_blocks.append((sc_times[i], sc_times[j]))
                        i = j
                        found_block = True
                        break
                if not found_block:
                    i += 1
                    
            merged_blocks = []
            for block in cm_blocks:
                if not merged_blocks:
                    merged_blocks.append(block)
                else:
                    last_block = merged_blocks[-1]
                    if abs(last_block[1] - block[0]) <= 1.5:
                        merged_blocks[-1] = (last_block[0], block[1])
                    else:
                        merged_blocks.append(block)

            if not merged_blocks:
                return False

            # チャプターテキストの構築
            chapters_text = []
            chapter_num = 1
            
            # 最初がCMでなければ0秒地点に「本編」を打つ
            if merged_blocks[0][0] > 0.5:
                chapters_text.append(f"CHAPTER{chapter_num:02d}=00:00:00.000")
                chapters_text.append(f"CHAPTER{chapter_num:02d}NAME=本編")
                chapter_num += 1

            for start, end in merged_blocks:
                chapters_text.append(f"CHAPTER{chapter_num:02d}={self.__format_time(start)}")
                chapters_text.append(f"CHAPTER{chapter_num:02d}NAME=CM")
                chapter_num += 1
                
                chapters_text.append(f"CHAPTER{chapter_num:02d}={self.__format_time(end)}")
                chapters_text.append(f"CHAPTER{chapter_num:02d}NAME=本編")
                chapter_num += 1

            chapter_file_path = self.file_path.with_name(f"{self.file_path.stem}.chapter.txt")
            # ★修正：await を追加
            await chapter_file_path.write_text('\n'.join(chapters_text), encoding='utf-8')
            logging.info(f'{self.file_path}: Successfully generated .chapter.txt from fallback detection.')
            return True

        except Exception as e:
            logging.error(f'Fallback CM detection failed: {e}', exc_info=e)
            return False


    async def __detectFromChapterFile(self) -> list[schemas.CMSection] | None:
        """
        録画ファイルに対応するチャプターファイルがもしあれば解析し、CM 区間情報を取得する
        """
        chapter_file_path = self.file_path.with_name(f"{self.file_path.stem}.chapter.txt")

        if not await chapter_file_path.exists():
            return None

        try:
            async with await chapter_file_path.open(encoding='utf-8') as f:
                lines = await f.readlines()
        except Exception as ex:
            logging.error(f'{chapter_file_path}: Failed to read chapter file:', exc_info=ex)
            return None

        chapters: list[tuple[int, str, float]] = []
        cm_sections: list[schemas.CMSection] = []

        for i in range(0, len(lines), 2):
            if i + 1 >= len(lines):
                break

            time_line = lines[i].strip()
            name_line = lines[i + 1].strip()

            if not (time_line.startswith('CHAPTER') and name_line.startswith('CHAPTER') and 'NAME' in name_line):
                continue

            try:
                chapter_num = int(time_line[7:9])
                chapter_time = self.__timeToSeconds(time_line.split('=')[1])
                chapter_name = name_line.split('=')[1]

                if chapter_time <= float(self.duration_sec):
                    chapters.append((chapter_num, chapter_name, chapter_time))
                else:
                    logging.warning(f'{chapter_file_path}: Chapter time {chapter_time} exceeds the video duration {self.duration_sec}. Skipping.')
            except Exception as ex:
                logging.warning(f'{chapter_file_path}: Failed to parse chapter data. (line {i}-{i+1}): {time_line}, {name_line}', exc_info=ex)
                return None

        current_cm_start: float | None = None

        for i, (_, name, ctime) in enumerate(chapters):
            if name.startswith('CM') and current_cm_start is None:
                current_cm_start = ctime
            elif not name.startswith('CM') and current_cm_start is not None:
                cm_sections.append({
                    'start_time': current_cm_start,
                    'end_time': ctime,
                })
                current_cm_start = None

        if current_cm_start is not None:
            cm_sections.append({
                'start_time': current_cm_start,
                'end_time': float(self.duration_sec),
            })

        return cm_sections


    @staticmethod
    def __timeToSeconds(time_str: str) -> float:
        hours, minutes, seconds = time_str.strip().split(':')
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)

    @staticmethod
    def __format_time(seconds: float) -> str:
        """秒数を HH:MM:SS.mmm 形式にフォーマットする"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds % 1) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


if __name__ == "__main__":
    def main(
        file_path: pathlib.Path = typer.Argument(
            ...,
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="録画ファイルのパス",
        ),
    ) -> None:
        LoadConfig(bypass_validation=True)

        from app.metadata.MetadataAnalyzer import MetadataAnalyzer
        analyzer = MetadataAnalyzer(file_path)
        recorded_program = analyzer.analyze()
        if recorded_program is None:
            print(f'Error: {file_path} is not a valid recorded file.')
            return

        detector = CMSectionsDetector(
            file_path = anyio.Path(recorded_program.recorded_video.file_path),
            duration_sec = recorded_program.recorded_video.duration,
        )

        asyncio.run(detector.detectAndSave())

    typer.run(main)
