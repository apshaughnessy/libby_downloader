# libby_downloader

This process allows you to download and parse Libby audiobooks by generating a HAR file, and downloading the MP3 files
directly.

## Requirements

- `ffmpeg` binary
- `requirements.txt` dependencies

## Process

This process will download the MP3 files, and split them into chapters using ffmpeg.

### Generate the HAR file

1. Checkout an audiobook
2. Open the audiobook
3. Open a Developer Tools, and switch to the Network tab
4. Disable caching in the Developer Tools (tick-box)
5. Navigate to the start of the audiobook and pause
6. Clear the current network log
7. Back in Libby, click the 'Next Chapter' button until you reach the end of the book
8. Download/export the HAR file (remember where you put it)

### Execute the script

1. Execute the script with the necessary arguments
   ```bash
   python main.py --har_file <NAME OF THE HAR FILE> --name "<ROOT NAME FOR DOWNLOAD FILES>" --title "<AUDIOBOOK TITLE>" \
   --author "<AUDIOBOOK AUTHOR>" --narrator "<AUDIOBOOK NARRATOR>"
   ```

### Additional parameters

- --has_introduction -> The MP3 files will contain an 'Introduction' chapter
  - This chapter will occur before the prologue
- --has_prologue -> The MP3 files will contain an 'Prologue' chapter
  - This chapter will occur after the introduction
- --has_epilogue -> The MP3 files will contain an 'Epilogue' chapter
  - This chapter will occur just before the conclusion
- --has_conclusion -> The MP3 files will contain an 'Conclusion' chapter
  - This chapter will occur after the epilogue
- --log_level {INFO,DEBUG,WARNING}
  - Level to use for Logging. Default level is WARNING, which is the least verbose
- -sdt SILENCE_DURATION_THRESHOLD, --silence_duration_threshold SILENCE_DURATION_THRESHOLD
  - The length of the silence to use for chapter detection
- -sdbt SILENCE_DB_THRESHOLD, --silence_db_threshold SILENCE_DB_THRESHOLD
   -The sound level expressed in dB to use for silence detection
- -ms MAXIMUM_SILENCE, --maximum_silence MAXIMUM_SILENCE
   - The length, in seconds, that a silence should be considered. This is helpful to remove trailing silences from an audio file.
- --no_chapters,
   - Specify whether to generate chapters from the audiobook. When provided the original Libby parts (~1 hour each) will be output

### Troubleshooting

#### HAR Expired

The files from Libby expire quickly, so you may need to re-generate the HAR file if you wait too long to process it

#### Chapters not detected correctly

- Try adjusting the `silence_duration_threshold` (the length of the silence between chapters) and/or the
  `silence_db_threshold` (How much noise to allow for a silence)

### No chapters detected

- Make sure your `maximum_silence` argument is greater than the `silence_duration_threshold` argument
