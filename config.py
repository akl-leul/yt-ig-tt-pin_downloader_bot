import os
from pathlib import Path

# Bot Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Download Configuration
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
DOWNLOADS_DIR = Path("downloads")

# YouTube Download Options
YDL_OPTS = {
    'format': 'best[height<=720]/best[height<=720]+bestaudio/best',
    'outtmpl': str(DOWNLOADS_DIR / '%(title)s.%(ext)s'),
    'restrictfilenames': True,
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
}

# Search Options
SEARCH_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'skip_download': True,
    'noplaylist': True,
}
