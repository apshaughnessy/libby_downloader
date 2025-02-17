import argparse
import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError

import coloredlogs


class AudioBooker:
    def __init__(self) -> None:
        self._args = self._process_args()
        self._logger = logging.getLogger(__name__)
        # Configure coloredlogs
        coloredlogs.install(
            level=self._args.log_level,
            fmt=r"{asctime:s} | {levelname:^8s} | {name:s} | {module:s}:{funcName:s}:{lineno:d} | {message:s}",
            datefmt="%Y-%m-%dT%H:%M:%S",
            style=r"{",
            level_styles={
                "info": {"color": "green"},
                "debug": {"color": "black", "bright": True},
                "warning": {"color": "yellow"},
                "error": {"color": "red", "bold": True},
                "critical": {"color": "magenta", "bold": True},
            },
            field_styles={
                "asctime": {"color": "white"},
                "levelname": {"color": "blue", "bold": True},
                "message": {"color": "white"},
                "module": {"color": "cyan"},
                "funcName": {"color": "cyan"},
                "lineno": {"color": "cyan"},
            },
        )
        self._metadata = self._build_metadata()
        self._download_directory = "download"
        self._logger.debug(f"Ensuring the {self._download_directory} directory exists")
        self._chapter = 0
        self._track = 0
        self._introduction_processed = False
        self._prologue_processed = False
        # This is used to add silence to the ends of each chapter for a better listening experience
        self._silence_padding = self._args.silence_duration_threshold / 2
        Path(self._download_directory).mkdir(parents=True, exist_ok=True)

    def _process_args(self):
        parser = argparse.ArgumentParser(description="Example script to demonstrate command-line argument parsing.")
        # Define command-line arguments
        parser.add_argument("--name", type=str, help="The name of the book", required=True)
        parser.add_argument("--title", type=str, help="The title of the book", required=True)
        parser.add_argument("--author", type=str, help="The author of the book", required=True)
        parser.add_argument("--narrator", type=str, help="The narrator of the book", required=True)
        parser.add_argument(
            "-hf", "--har_file", type=str, help="The path to the generated HAR file", default="libbyapp.com.har"
        )
        parser.add_argument(
            "--has_introduction",
            type=bool,
            help="Specify whether the audiobook has an introduction before the prologue/first chapter for chapter generation purposes",
            default=False,
        )
        parser.add_argument(
            "--has_prologue",
            type=bool,
            help="Specify whether the audiobook has an prologue before the first chapter for chapter generation purposes",
            default=False,
        )
        parser.add_argument(
            "--has_epilogue",
            type=bool,
            help="Specify whether the audiobook has an epilogue for chapter generation purposes",
            default=False,
        )
        parser.add_argument(
            "--has_conclusion",
            type=bool,
            help="Specify whether the audiobook has a conclusion following the epilogue for chapter generation purposes",
            default=False,
        )
        parser.add_argument(
            "--log_level",
            type=str,
            help="Level to use for Logging",
            default="INFO",
            choices=["INFO", "DEBUG", "WARNING"],
        )
        parser.add_argument(
            "-sdt",
            "--silence_duration_threshold",
            type=float,
            help="The length of the silence to use for chapter detection",
            default=3.5,
        )
        parser.add_argument(
            "-sdbt",
            "--silence_db_threshold",
            type=float,
            help="The DB threshold to use for chapter detection",
            default=-35,
        )
        parser.add_argument(
            "-ms",
            "--maximum_silence",
            type=float,
            help="The length, in seconds, that a silence should be considered. This is helpful to remove trailing silences from a audio file.",
            default=5,
        )
        parser.add_argument(
            "--no_chapters",
            action=argparse.BooleanOptionalAction,
            help="Specify whether to generate chapters from the audiobook",
            default=False,
        )

        # Parse the arguments
        return parser.parse_args()

    def _build_metadata(self) -> str:
        metadata = f'-metadata album="{self._args.title}" -metadata author="{self._args.author}" -metadata album_artist="{self._args.author}"'
        if self._args.composer:
            metadata += f' -metadata composer="{self._args.composer}"'
        return metadata

    def _execute_command(self, command: str) -> subprocess.CompletedProcess:
        self._logger.info(f"Executing: {command}")
        result = subprocess.run(
            command,
            shell=True,
            encoding="utf-8",
            capture_output=True,
        )
        self._logger.debug(result)
        if result.returncode != 0:
            self._logger.critical(f"ERROR: Error in command -> {result.stderr}")
            sys.exit(1)
        return result

    def _detect_silences(self, filename: str) -> list[tuple[str]]:
        self._logger.info(f"Attempting to parse timestamps for silences between chapters from the {filename} file")
        # output is in stderr, so it needs to be piped to stdout (2>&1)
        command = f'ffmpeg -i "{filename}" -af silencedetect=n={self._args.silence_db_threshold}dB:d={self._args.silence_duration_threshold} -f null - 2>&1'

        result = self._execute_command(command=command)
        self._logger.debug(f"Locating silence timestamps from output:\n{result.stdout}")

        timestamps = re.findall(
            r"^\[silencedetect.*silence_start: ([\d\.]+)\n\[silencedetect.+silence_end: ([\d\.]+) \| silence_duration: ([\d\.]+)",
            result.stdout,
            re.MULTILINE,
        )
        # Filter out any silences that exceed the maximum threshold
        timestamps = [(ts[0], ts[1]) for ts in timestamps if float(ts[2]) < float(self._args.maximum_silence)]
        self._logger.info(f"Located silence time periods -> {timestamps}")
        return timestamps

    def _load_har_file(self) -> dict:
        self._logger.info(f"Loading data from the {self._args.har_file} HAR file")
        with open(self._args.har_file, "r") as libby_har:
            loaded_har_file = json.load(libby_har)
            self._logger.debug(f"Loaded HAR data:\n{loaded_har_file}")
            return loaded_har_file

    def _identify_download_urls(self, loaded_har_file: dict) -> list[str]:
        self._logger.info("Attempting to parse the MP3 download URLS from the loaded HAR file")
        located_media_links = {}
        for libby_entry in loaded_har_file["log"]["entries"]:
            if libby_entry["_resourceType"] != "media":
                self._logger.debug(f"Skipping non-media resource: {libby_entry['_resourceType']}")
                continue
            if "odrmediaclips.cachefly.net" not in libby_entry["request"]["url"]:
                self._logger.debug(f"Skipping invalid URL: {libby_entry['request']['url']}")
                continue
            query_timestamp = datetime.fromisoformat(libby_entry["startedDateTime"].replace("Z", "+00:00"))
            if (timestamp := located_media_links.get(libby_entry["request"]["url"])) and query_timestamp < timestamp:
                self._logger.debug(f"Skipping older timestamp: {libby_entry['startedDateTime']}")
                continue
            self._logger.debug(f"Appending URL: {libby_entry['request']['url']}")
            located_media_links[libby_entry["request"]["url"]] = query_timestamp

        # Create a list of names ordered by their timestamps
        # This ensures that the parts are sorted so that the identified chapters are in order
        ordered_media_links = [name for name, _ in sorted(located_media_links.items(), key=lambda item: item[1])]
        self._logger.debug(f"Ordered URL list: {ordered_media_links}")

        return ordered_media_links

    def _download_audiobook_files(self, located_media_links: list) -> list[str]:
        downloaded_files = []
        self._logger.info(f"Attempting to download the MP3 files from the {self._args.har_file} HAR file")
        self._logger.debug(f"Downloading: {json.dumps(list(located_media_links), indent=4)}")
        for media_link in located_media_links:
            re_match = re.search(r"\/(\w+)$", media_link)
            if not re_match:
                self._logger.error(f"Unable to parse filename from {media_link}")
                continue
            filename = re_match.group(1)
            self._logger.info(f"Parsed filename {filename} from URL")
            download_file = f"{self._download_directory}/{self._args.name}_{filename}.mp3"
            if os.path.isfile(download_file):
                self._logger.warning(f"The {download_file} file has already been downloaded")
                downloaded_files.append(download_file)
                continue
            self._logger.info(f"Downloading from: {media_link} to {download_file}")
            try:
                urllib.request.urlretrieve(media_link, download_file)
                downloaded_files.append(download_file)
            except HTTPError:
                self._logger.error(
                    f"The download link for the {download_file} file is expired. Regenerate a new HAR file."
                )
            except Exception:
                self._logger.exception(f"Unhandled error while downloading {download_file}")
        return downloaded_files

    def _generate_chapter_file(self, filename: str, start_time: int, end_time: int, chapter: str, track: int) -> None:
        output_file = f"{self._download_directory}/{self._args.name}_{chapter}.mp3"
        self._logger.info(f"Generating the {output_file} chapter")
        metadata = f'{self._metadata} -metadata title="{chapter}" -metadata track="{track}"'
        command = f'ffmpeg -i "{filename}" {metadata} -c copy -y'
        if start_time:
            command += f" -ss {float(start_time) - self._silence_padding}"
        if end_time:
            command += f" -to {float(end_time) + self._silence_padding}"
        # The outfile file must be the last argument in the command
        command += f' "{output_file}"'
        if os.path.isfile(output_file):
            self._logger.warning(f"The {output_file} chapter file has already been generated")
        self._execute_command(command=command)

    def _identify_chapters(self, filename: str, silence_timestamps: tuple[str], is_last_file: bool) -> None:
        self._logger.info(f"Attempting to parse chapters from the {filename} file")

        next_chapter_start_time = 0
        for silence_timestamp in silence_timestamps:
            self._logger.debug(f"Processing silence timestamp: {silence_timestamp}")
            silence_start, silence_end = silence_timestamp
            if self._args.has_introduction and not self._introduction_processed:
                chapter = "Introduction"
                self._introduction_processed = True
            elif self._args.has_prologue and not self._prologue_processed:
                chapter = "Prologue"
                self._prologue_processed = True
            # If the last chapter WITH a conclusion following in the LAST downloaded file
            elif silence_timestamp == silence_timestamps[-1] and self._args.has_conclusion and is_last_file:
                chapter = "Epilogue"
            else:
                self._chapter += 1
                chapter = f"Chapter {self._chapter}"
            self._track += 1
            self._generate_chapter_file(
                filename=filename,
                start_time=next_chapter_start_time,
                end_time=silence_start,
                chapter=chapter,
                track = self._track,
            )
            next_chapter_start_time = silence_end
        # This captures all of the audio AFTER the last detected silence, which should be the rest of a MP3 file
        if is_last_file:
            if self._args.has_conclusion:
                chapter = "Conclusion"
            else:
                chapter = "Epilogue"
        else:
            self._chapter += 1
            if self._args.no_chapters:
                chapter = f"Part {self._chapter}"
            else:
                chapter = f"Chapter {self._chapter}"
        self._track += 1
        self._generate_chapter_file(
            filename=filename,
            start_time=next_chapter_start_time,
            end_time=0,
            chapter=chapter,
            track = self._track 
        )

    def execute(self):
        self._logger.info(f"Generating MP3 files for the {self._args.name} Audiobook")
        loaded_har_file = self._load_har_file()
        located_media_links = self._identify_download_urls(loaded_har_file=loaded_har_file)
        downloaded_files = self._download_audiobook_files(located_media_links=located_media_links)
        for downloaded_file in downloaded_files:
            if self._args.no_chapters:
                silence_timestamps = []
            else:
                silence_timestamps = self._detect_silences(filename=downloaded_file)
            self._identify_chapters(
                filename=downloaded_file,
                silence_timestamps=silence_timestamps,
                is_last_file=self._args.has_epilogue and downloaded_file == downloaded_files[-1],
            )


ab = AudioBooker()
ab.execute()
