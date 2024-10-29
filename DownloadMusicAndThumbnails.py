import yt_dlp
import sys
#import os
#import easygui

URLS = sys.argv[1]
location = sys.argv[2]

ydl_opts = {
    'writethumbnail': True,
    'extract-audio': True,
    'add-metadata': True,
    'timeout': 25,
    'ignoreerrors': True,
    #'embed-thumbnail': True,

    'outtmpl': location+'/%(title)s.%(ext)s',

    'ffmpeg-location': r"/Library/Frameworks/Python.framework/Versions/3.12/bin/",
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
    },{
        'key': 'EmbedThumbnail',
    },],
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    error_code = ydl.download(URLS)