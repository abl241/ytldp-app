import yt_dlp
import sys
#import os
#import easygui

URLS = sys.argv[1]
location = sys.argv[2]

ydl_opts = {
    'format': 'bestvideo+bestaudio[ext=m4a]/best[ext=mp4]/best',  # Ensure MP4 is preferred
    'merge_output_format': 'mp4',   # Force output as MP4
    'outtmpl': location+'/%(title)s.%(ext)s',
    'add-metadata': True,           # Add metadata to the video
    'ffmpeg-location': r"ffmpeg", #/Library/Frameworks/Python.framework/Versions/3.12/bin/
    'ignoreerrors': True,           # Skip broken videos
    'retries': 3,                   # Retry failed downloads
    'timeout': 60,
    'writethumbnail': True,
    'embedthumbnail': True,
    'postprocessors': [{
        'key': 'EmbedThumbnail',
    },],
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    error_code = ydl.download(URLS)