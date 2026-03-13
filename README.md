# 🎬 Multi-Platform Media Downloader Bot

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Aiogram](https://img.shields.io/badge/framework-aiogram_3.x-red.svg)](https://docs.aiogram.dev/)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/)

A powerful, asynchronous Telegram bot built with **aiogram 3** that simplifies downloading media from **YouTube, Instagram, TikTok, and Pinterest**. No API keys required for most platforms—just send a link or a search query!

---

## ✨ Key Features

- 📺 **Multi-Platform Support**: Download from YouTube, Instagram (Reels/Posts), TikTok (No-Watermark), and Pinterest (Video/Image).
- ⚙️ **Quality Selection**: Choose between 360p, 480p, and 720p for YouTube videos to manage data and file size.
- 🔍 **Search & Download**: Send a title directly to search YouTube and get download options.
- ⚡ **Inline Mode**: Use `@your_bot_name` in any chat to search and share videos instantly.
- 🎨 **Progress Tracking**: Real-time progress bars and speed monitoring during downloads.
- 🛠️ **Smart Quality Selection**: Automatically selects the best quality (up to 720p) within Telegram's file size limits.
- 🧹 **Zero Configuration Cleanup**: Automatically manages a local `downloads/` folder and cleans up after sending.
- 🚀 **Full Async Engine**: High-performance processing using `asyncio` and `yt-dlp`.

---

## 🚀 Deployment Guide

### 1. Prerequisites
- **Python 3.11+** installed on your system.
- A **Telegram Bot Token** from [@BotFather](https://t.me/BotFather).
- (Optional) **FFmpeg** installed (recommended for better video merging).

### 2. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/yourusername/YT_downloader_Bot.git
cd YT_downloader_Bot
pip install -r requirements.txt
```

### 3. Configuration
Create a `.env` file in the root directory:
```env
BOT_TOKEN=your_telegram_bot_token_here
```

### 4. Running the Bot
```bash
python main.py
```

---

## 📱 Supported Platforms & Usage

| Platform | URL Pattern | Feature |
| :--- | :--- | :--- |
| **YouTube** | `youtube.com/watch?v=...` | Video search, 720p downloads, Shorts support |
| **Instagram** | `instagram.com/reels/...` | Single post downloads (via Instaloader) |
| **TikTok** | `tiktok.com/@user/video/...` | No-watermark video downloads |
| **Pinterest** | `pinterest.com/pin/...` | Image and Video extraction |

### Modes
- **Direct Link**: Just paste the URL from any supported site.
- **Search**: Send any text (e.g., "Interstellar Soundtrack") to get a search result list.
- **Inline**: Type `@bot_name query` in any chat to find and share videos.

---

## 🛠️ Technical Details

- **Framework**: `aiogram` (v3.x) for the Telegram interface.
- **Extraction**: `yt-dlp` for YouTube/General, `instaloader` for Instagram, `tiktok_downloader` for TikTok.
- **Storage**: Temporary files are stored in `./downloads` and purged immediately after the upload is finished.
- **Size Limits**: Automatically detects if a file exceeds Telegram's **50MB** limit. It will fallback to lower resolutions (e.g., 480p) to try and stay within the limit.

---

## ⚠️ Disclaimer

This project is for **educational purposes only**. Please respect the terms of service of each platform and the copyright of the content creators. The developers are not responsible for any misuse of this tool.

---

## 📜 License
Distributed under the MIT License. See `LICENSE` for more information.
