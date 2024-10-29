import yt_dlp
import sys
import os

URLS = sys.argv[1]
location = sys.argv[2]

message = r'yt-dlp --skip-download --write-thumbnail --ffmpeg-location "ffmpeg" --convert-thumbnails "jpg" ' #C:\Users\Alex\Music\W\code\ffmpeg-master-latest-win64-gpl\bin

os.chdir(location)
os.system(message + URLS)



# URLS = ['https://youtu.be/KfEEm4Zx-EU?si=pds3Kop9OnkAtnLr', 'https://youtu.be/39IU7ADaXmQ?si=kSFOrrE4CYtCggTK']
# location = r'C:\Users\Alex\Music\W\code\test'
# ydl_opts = {
#     'format': 'm4a/bestaudio/best',
#     'ffmpeg-location': r'C:\Users\Alex\Music\W\code\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe',
#     # ℹ️ See help(yt_dlp.postprocessor) for a list of available Postprocessors and their arguments
#     'postprocessors': [{  # Extract audio using ffmpeg
#         'key': 'FFmpegExtractAudio',
#         'preferredcodec': 'mp3',
#     }],
#     #'outtmpl':r"C:\Users\Alex\Music\W\code\test"
# }

# with yt_dlp.YoutubeDL(ydl_opts) as ydl:
#     error_code = ydl.download(URLS)