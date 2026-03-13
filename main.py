import asyncio
import os
import logging
import tempfile
import re
import time
import aiohttp
import instaloader
import tiktok_downloader
import pinterest_downloader
from pathlib import Path
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiohttp import web
from aiogram.filters import Command
from aiogram.types import InlineQueryResultArticle, InputTextMessageContent
import yt_dlp
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_SERVER_URL = os.getenv("TELEGRAM_API_URL") # For Local Bot API Server support (up to 2GB)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")

# Constants - Dynamic based on API Server
IS_LOCAL_API = bool(API_SERVER_URL)
MAX_FILE_SIZE_MB = 2000 if IS_LOCAL_API else 50 # Local API supports up to 2GB, Cloud API 50MB
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

# URL Patterns
YOUTUBE_URL_PATTERN = re.compile(
    r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|shorts/|.+\?v=)?([^&=%\?/\s]{11})'
)
INSTAGRAM_URL_PATTERN = re.compile(r'instagram\.com/(p|reel|tv|reels)/')
TIKTOK_URL_PATTERN = re.compile(r'tiktok\.com/')
PINTEREST_URL_PATTERN = re.compile(r'pinterest\.com/pin/|pin\.it/')

# Combined pattern for detection
URL_PATTERN = re.compile(r'https?://[^\s]+')


class YouTubeDownloader:
    def __init__(self):
        self.ydl_opts = {
            'format': 'best[ext=mp4]/best', # Single file format (no FFmpeg needed)
            'outtmpl': str(DOWNLOADS_DIR / '%(title)s.%(ext)s'),
            'restrictfilenames': True,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'no_color': True,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'nopart': True, # Disable .part files to avoid WinError 32 on Windows
        }
        self.current_message = None
        self.last_update_time = 0
        self.loop = None

    def progress_hook(self, d):
        """Hook for yt-dlp to report progress"""
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%', '').strip()
            try:
                percent = float(p)
            except ValueError:
                percent = 0
            
            # Throttle updates to once every 4 seconds to avoid Telegram rate limits
            now = time.time()
            if now - self.last_update_time < 4:
                return

            bar_length = 10
            filled_length = int(bar_length * percent / 100)
            bar = '█' * filled_length + '░' * (bar_length - filled_length)
            
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            progress_text = (
                f"⏳ <b>Downloading...</b>\n\n"
                f"<code>[{bar}]</code> {percent:.1f}%\n"
                f"🚀 Speed: {speed}\n"
                f"🕒 ETA: {eta}"
            )
            
            if self.current_message and self.loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.current_message.edit_text(progress_text),
                        self.loop
                    )
                    self.last_update_time = now
                except Exception as e:
                    logger.debug(f"Progress update error: {e}")

    async def search_videos(self, query: str, max_results: int = 5) -> Optional[list]:
        """Search for multiple videos and return list of video info"""
        try:
            search_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'noplaylist': True,
                'extract_flat': True,  # Faster search by not extracting full info
            }
            
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                # Quote the query to avoid issues with special characters
                search_query = f"ytsearch{max_results}:{query}"
                info = await asyncio.to_thread(ydl.extract_info, search_query, download=False)
                
                if info and 'entries' in info:
                    videos = []
                    for video_info in info['entries']:
                        if video_info:
                            video_id = video_info.get('id')
                            if not video_id:
                                continue
                            videos.append({
                                'title': video_info.get('title', 'Unknown'),
                                'thumbnail': video_info.get('thumbnail', ''),
                                'id': video_id,
                                'duration': video_info.get('duration', 0),
                                'uploader': video_info.get('uploader', ''),
                                'url': f"https://www.youtube.com/watch?v={video_id}",
                                'view_count': video_info.get('view_count', 0)
                            })
                    return videos
        except Exception as e:
            logger.error(f"Error searching for videos: {e}")
        
        return None

    async def check_file_size(self, video_url: str) -> Optional[int]:
        """Check the estimated file size of the video"""
        try:
            opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'noplaylist': True,
                'format': 'best',
            }
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, video_url, download=False)
                
                if info and 'filesize' in info:
                    return info['filesize']
                elif info and 'requested_formats' in info:
                    # Calculate total size for merged video+audio
                    total_size = 0
                    for fmt in info['requested_formats']:
                        if 'filesize' in fmt:
                            total_size += fmt['filesize']
                    return total_size if total_size > 0 else None
        except Exception as e:
            logger.error(f"Error checking file size: {e}")
        
        return None

    async def download_video(self, video_url: str, video_info: Optional[dict] = None, progress_msg=None, target_quality: Optional[str] = None) -> Optional[str]:
        """Download video and return file path. Uses specialized libraries where requested."""
        try:
            self.current_message = progress_msg
            self.last_update_time = 0
            self.loop = asyncio.get_running_loop()
            estimated_size = None
            
            # 1. Handle Instagram with instaloader
            if "instagram.com" in video_url:
                try:
                    L = instaloader.Instaloader(dirname_pattern=str(DOWNLOADS_DIR), download_video_thumbnails=False, save_metadata=False, quiet=True)
                    # Extract shortcode
                    shortcode_match = re.search(r'/(?:p|reels|reel|tv)/([^/?#&]+)', video_url)
                    if shortcode_match:
                        shortcode = shortcode_match.group(1)
                        post = instaloader.Post.from_shortcode(L.context, shortcode)
                        L.download_post(post, target=str(DOWNLOADS_DIR))
                        # Find the downloaded file
                        for f in os.listdir(DOWNLOADS_DIR):
                            if shortcode in f and f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png')):
                                return str(DOWNLOADS_DIR / f)
                except Exception as e:
                    logger.error(f"Instaloader failed: {e}")
                    # Fallback to yt-dlp happens below

            # 2. Handle TikTok with tiktok_downloader
            if "tiktok.com" in video_url:
                try:
                    # tiktok_downloader.tikwm(url) returns a list of media objects
                    results = tiktok_downloader.tikwm(video_url)
                    if results:
                        # Find the first video (usually watermark-free)
                        video_data = next((r for r in results if r.type == "video"), None)
                        if video_data:
                            file_path = str(DOWNLOADS_DIR / f"tiktok_{int(time.time())}.mp4")
                            video_data.download(file_path)
                            return file_path
                except Exception as e:
                    logger.error(f"tiktok_downloader failed: {e}")

            # 3. Handle Pinterest with pinterest_downloader
            if "pinterest.com" in video_url or "pin.it" in video_url:
                try:
                    p = pinterest_downloader.Pinterest()
                    pin = p.get_pin(video_url)
                    media_url = pin.get('video') or pin.get('image')
                    if media_url:
                        ext = '.mp4' if pin.get('video') else '.jpg'
                        file_path = str(DOWNLOADS_DIR / f"pinterest_{int(time.time())}{ext}")
                        async with aiohttp.ClientSession() as session:
                            async with session.get(media_url) as resp:
                                if resp.status == 200:
                                    with open(file_path, 'wb') as f:
                                        f.write(await resp.read())
                                    return file_path
                except Exception as e:
                    logger.error(f"pinterest_downloader failed: {e}")

            # 4. Default / Fallback to yt-dlp (YouTube + others)
            opts = self.ydl_opts.copy()
            opts['progress_hooks'] = [self.progress_hook]
            
            # Smart Format Selection for Size Control
            if "youtube.com" in video_url or "youtu.be" in video_url:
                if target_quality:
                    # Use specific quality if requested
                    opts['format'] = f'best[height<={target_quality}][ext=mp4]/best[ext=mp4]/best'
                    logger.info(f"Downloading with target quality: {target_quality}p")
                else:
                    # Check size if possible for auto selection
                    estimated_size = await self.check_file_size(video_url)
                    if estimated_size and estimated_size > MAX_FILE_SIZE_BYTES:
                        # Fallback to lower resolution (480p) if 720p is too large
                        opts['format'] = 'best[height<=480][ext=mp4]/best[ext=mp4]/best'
                        logger.info(f"Video large ({estimated_size/1024/1024:.1f}MB), falling back to 480p")
                    else:
                        # Try 720p as default
                        opts['format'] = 'best[height<=720][ext=mp4]/best[ext=mp4]/best'
            
            # Check size again or for other platforms
            estimated_size = estimated_size if (("youtube.com" in video_url or "youtu.be" in video_url) and not target_quality) else await self.check_file_size(video_url)
            if estimated_size and estimated_size > MAX_FILE_SIZE_BYTES + (5 * 1024 * 1024): # 5MB buffer
                logger.warning(f"File likely too large: {estimated_size / (1024*1024):.1f}MB")
                # We return None early to save bandwidth/time
                return None
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, video_url, download=True)
                if not info:
                    return None
                    
                filename = ydl.prepare_filename(info)
                # yt_dlp might change extension
                if not os.path.exists(filename):
                    base = os.path.splitext(filename)[0]
                    for f in DOWNLOADS_DIR.glob(f"{os.path.basename(base)}.*"):
                        if f.suffix.lower() in ['.mp4', '.mkv', '.webm', '.jpg', '.jpeg', '.png', '.webp']:
                            filename = str(f)
                            break
                
                if os.path.exists(filename):
                    actual_size = os.path.getsize(filename)
                    if actual_size > MAX_FILE_SIZE_BYTES + (1024*1024): # 1MB buffer
                        logger.warning(f"Downloaded file still too large: {actual_size/1024/1024:.1f}MB")
                        # We'll let the sending logic handle the final decision/error
                    return filename
            return None
        except Exception as e:
            logger.error(f"Error in download_video: {e}")
            return None

    async def cleanup_file(self, file_path: str):
        """Clean up downloaded file"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up file: {file_path}")
        except Exception as e:
            logger.error(f"Error cleaning up file: {e}")


# Initialize downloader
downloader = YouTubeDownloader()

# Initialize bot with Local Bot API support if configured
bot_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
if IS_LOCAL_API:
    logger.info(f"Using Local Bot API Server: {API_SERVER_URL}")
    bot = Bot(
        token=BOT_TOKEN,
        default=bot_properties,
        session=aiohttp.ClientSession(), # Required for custom server in some versions
        server=TelegramAPIServer.from_base(API_SERVER_URL)
    )
else:
    bot = Bot(
        token=BOT_TOKEN,
        default=bot_properties
    )

dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Handle /start command"""
    await message.answer(
        "🎬 <b>YouTube Downloader Bot</b>\n\n"
        "Send me a video title to search and download it in 720p quality.\n"
        "You can also use inline mode: @leulytbot video title\n\n"
        f"Maximum file size: {MAX_FILE_SIZE_MB}MB"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Handle /help command"""
    await message.answer(
        "📖 <b>How to use this bot:</b>\n\n"
        "1. <b>Direct Message:</b> Just send a video title\n"
        "2. <b>Inline Mode:</b> Type @leulytbot video title in any chat\n\n"
        "The bot will search YouTube and download the best quality up to 720p.\n"
        f"Files larger than {MAX_FILE_SIZE_MB}MB cannot be sent via Telegram."
    )


@dp.message(F.text)
async def handle_text_message(message: types.Message):
    """Handle text messages for search or direct links"""
    text = message.text.strip()
    
    # Improved link extraction
    links = re.findall(r'https?://[^\s{}|<>\"\'\[\]\^`]+', text)
    if links:
        url = links[0]
        
        # Determine platform for better feedback
        platform = "Video"
        if YOUTUBE_URL_PATTERN.search(url):
            # For YouTube, show quality options first
            video_id = YOUTUBE_URL_PATTERN.search(url).group(6)
            keyboard = [
                [
                    types.InlineKeyboardButton(text="📺 360p (Data Saver)", callback_data=f"ql_{video_id}_360"),
                    types.InlineKeyboardButton(text="📺 480p (Standard)", callback_data=f"ql_{video_id}_480")
                ],
                [
                    types.InlineKeyboardButton(text="📺 720p (HD)", callback_data=f"ql_{video_id}_720")
                ]
            ]
            reply_markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
            await message.answer(
                f"🎥 <b>YouTube Video Detected</b>\n\n"
                f"Select your preferred quality to start downloading:",
                reply_markup=reply_markup
            )
        elif INSTAGRAM_URL_PATTERN.search(url):
            await process_video_download(message, url, platform="Instagram")
        elif TIKTOK_URL_PATTERN.search(url):
            await process_video_download(message, url, platform="TikTok")
        elif PINTEREST_URL_PATTERN.search(url):
            await process_video_download(message, url, platform="Pinterest")
        else:
            await process_video_download(message, url, platform="Video")
        return

    # Check if it's a download request from inline mode or legacy button
    if text.startswith("Download Request:"):
        video_title = text.replace("Download Request:", "").strip()
        await process_download_request(message, video_title)
    else:
        # Treat as search request - show results
        await show_search_results(message, text)


async def show_search_results(message: types.Message, query: str):
    """Show search results as inline keyboard buttons"""
    searching_msg = await message.answer("🔍 Searching for videos...")
    
    try:
        # Search for videos
        videos = await downloader.search_videos(query, max_results=5)
        
        if not videos:
            await searching_msg.edit_text("❌ No videos found for your search query.")
            return
        
        # Create inline keyboard
        keyboard = []
        for i, video in enumerate(videos, 1):
            # Truncate title if too long
            title = video['title'][:50] + "..." if len(video['title']) > 50 else video['title']
            duration = f"{video['duration']//60}:{video['duration']%60:02d}" if video['duration'] else "Unknown"
            
            button_text = f"{i}. {title} ({duration})"
            keyboard.append([types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"select_{video['id']}"
            )])
        
        # Add cancel button
        keyboard.append([types.InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_search")])
        
        reply_markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await searching_msg.edit_text(
            f"🎬 <b>Search Results for:</b> {query}\n\n"
            f"⚡️ Choose a video to download:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error in show_search_results: {e}")
        await searching_msg.edit_text("❌ An error occurred while searching.")


@dp.callback_query(lambda c: c.data.startswith('select_'))
async def handle_select_callback(callback: types.CallbackQuery):
    """Handle video selection and show quality options"""
    try:
        video_id = callback.data.split('_')[1]
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Create quality options keyboard
        keyboard = [
            [
                types.InlineKeyboardButton(text="📺 360p (Data Saver)", callback_data=f"ql_{video_id}_360"),
                types.InlineKeyboardButton(text="📺 480p (Standard)", callback_data=f"ql_{video_id}_480")
            ],
            [
                types.InlineKeyboardButton(text="📺 720p (HD)", callback_data=f"ql_{video_id}_720")
            ],
            [
                types.InlineKeyboardButton(text="🔙 Back to Search", callback_data="cancel_search")
            ]
        ]
        reply_markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(
            "🎬 <b>Select Video Quality:</b>\n\n"
            "Higher quality means larger file size. "
            "Note: Files over 50MB will be automatically downscaled or aborted.",
            reply_markup=reply_markup
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in handle_select_callback: {e}")
        await callback.answer("❌ Failed to load qualities", show_alert=True)


@dp.callback_query(lambda c: c.data.startswith('ql_'))
async def handle_quality_callback(callback: types.CallbackQuery):
    """Handle specific quality selection"""
    try:
        parts = callback.data.split('_')
        video_id = parts[1]
        quality = parts[2]
        
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        await callback.answer(f"⬇️ Downloading in {quality}p...")
        
        # Download the video with specific quality
        await process_video_download(callback.message, video_url, platform="YouTube", target_quality=quality)
        
    except Exception as e:
        logger.error(f"Error in handle_quality_callback: {e}")
        await callback.answer("❌ Download failed", show_alert=True)


@dp.callback_query(lambda c: c.data == 'cancel_search')
async def handle_cancel_callback(callback: types.CallbackQuery):
    """Handle cancel button click"""
    await callback.message.edit_text("❌ Search cancelled.")
    await callback.answer()


async def process_video_download(message: types.Message, video_url: str, platform: str = "Video", target_quality: Optional[str] = None):
    """Process video download"""
    try:
        # Send downloading message
        downloading_msg = await message.answer(f"⬇️ Detecting {platform} link...")
        
        async def get_info(url):
            # 1. Try specialized libraries for metadata first to avoid yt-dlp error noise
            if "pinterest.com" in url or "pin.it" in url:
                try:
                    # Resolve pin.it short links if necessary
                    if "pin.it" in url:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url, allow_redirects=True) as resp:
                                url = str(resp.url)
                    
                    p = pinterest_downloader.Pinterest()
                    pin = p.get_pin(url)
                    return {'title': pin.get('title') or 'Pinterest Pin', 'url': url, 'extractor': 'pinterest_downloader', 'thumbnails': [{'url': pin.get('image')}] if pin.get('image') else []}
                except:
                    # If specialized library fails, we still want to avoid the yt-dlp error noise
                    # so we return a fallback info object immediately
                    return {'title': 'Pinterest Content', 'url': url, 'extractor': 'fallback'}

            if "instagram.com" in url:
                try:
                    L = instaloader.Instaloader(download_video_thumbnails=False, save_metadata=False, quiet=True)
                    shortcode_match = re.search(r'/(?:p|reels|reel|tv)/([^/?#&]+)', url)
                    if shortcode_match:
                        post = instaloader.Post.from_shortcode(L.context, shortcode_match.group(1))
                        return {'title': f"Instagram Post by {post.owner_username}", 'url': url, 'extractor': 'instaloader'}
                except: pass

            if "tiktok.com" in url:
                try:
                    return {'title': 'TikTok Video', 'url': url, 'extractor': 'tiktok_downloader'}
                except: pass

            # 2. Fallback to yt-dlp for YouTube and others
            opts = {
                'quiet': True, 
                'no_warnings': True, 
                'skip_download': True, 
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'no_color': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return await asyncio.to_thread(ydl.extract_info, url, download=False)
            except Exception as e:
                err_msg = str(e).lower()
                # If it's a known non-video type or unsupported, handle gracefully
                if any(x in err_msg for x in ["no video formats found", "unsupported url", "content not available"]):
                    if any(domain in url for domain in ["pinterest.com", "pin.it", "instagram.com", "tiktok.com"]):
                        return {'title': 'Social Media Content', 'url': url, 'extractor': 'fallback'}
                raise e

        try:
            info = await get_info(video_url)
            video_title = info.get('title', 'Unknown Title')
        except Exception as e:
            logger.error(f"Metadata extraction failed: {e}")
            video_title = "Unknown Content"
            info = None
        
        await downloading_msg.edit_text(
            f"🎥 Downloading: <b>{video_title}</b>\n"
            f"⏳ <b>Processing...</b> 🔄"
        )
        
        # Download video or handle image if no formats found
        file_path = None
        try:
            file_path = await downloader.download_video(video_url, video_info=info, progress_msg=downloading_msg, target_quality=target_quality)
        except Exception as e:
            err_msg = str(e)
            is_pinterest = "pinterest.com" in video_url or "pin.it" in video_url
            
            if "No video formats found" in err_msg or is_pinterest or info:
                # Last resort image extraction
                img_url = None
                if info:
                    # Use info['url'] if it looks like a direct image and isn't YouTube
                    if info.get('url', '').startswith('http') and info.get('extractor') != 'youtube' and not info.get('formats'):
                        img_url = info.get('url')
                    if not img_url and info.get('thumbnails'):
                        img_url = info.get('thumbnails')[-1]['url']
                
                # If still no img_url and it's Pinterest, try manual scraping (aggressive fallback)
                if not img_url and is_pinterest:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(video_url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                                html = await resp.text()
                                # Common Pinterest image regex patterns
                                match = re.search(r'"images":{"orig":{"url":"([^"]+)"', html) or \
                                        re.search(r'"property="og:image" content="([^"]+)"', html) or \
                                        re.search(r'https://i\.pinimg\.com/originals/[^"]+', html)
                                if match:
                                    img_url = (match.group(1) if match.groups() else match.group(0)).replace('\\u0026', '&')
                    except Exception as scrape_err:
                        logger.debug(f"Pinterest scrape failed: {scrape_err}")

                if img_url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(img_url) as resp:
                                if resp.status == 200:
                                    content_type = resp.headers.get('Content-Type', '')
                                    if 'image' in content_type or any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                                        ext = os.path.splitext(img_url.split('?')[0])[1] or '.jpg'
                                        if len(ext) > 5 or '?' in ext: ext = '.jpg'
                                        file_path = str(DOWNLOADS_DIR / f"image_{int(time.time())}{ext}")
                                        with open(file_path, 'wb') as f:
                                            f.write(await resp.read())
                    except Exception as dl_err:
                        logger.debug(f"Image fallback download failed: {dl_err}")
            
            if not file_path:
                raise e
        
        if not file_path:
            await downloading_msg.edit_text(
                f"❌ Cannot download <b>{video_title}</b>\n"
                f"The content may be too large, private, or unavailable."
            )
            return
        
        await downloading_msg.edit_text(
            f"✅ <b>Downloaded!</b>\n"
            f"🚀 <b>Sending to you...</b> ⚡️"
        )
        
        # Determine media type and send
        file_ext = os.path.splitext(file_path)[1].lower()
        file_size = os.path.getsize(file_path)
        
        if file_size > MAX_FILE_SIZE_BYTES + (2 * 1024 * 1024): # 2MB grace
            await message.answer(
                f"⚠️ <b>File too large!</b>\n\n"
                f"The file size ({file_size/1024/1024:.1f}MB) exceeds Telegram's 50MB limit for bots.\n"
                f"Please try a shorter video or lower resolution."
            )
            await downloading_msg.delete()
            await downloader.cleanup_file(file_path)
            return

        try:
            if file_ext in ['.jpg', '.jpeg', '.png', '.webp']:
                await message.answer_photo(
                    types.FSInputFile(file_path),
                    caption=f"🖼 {video_title}"
                )
            elif file_ext in ['.mp4', '.mkv', '.webm', '.mov', '.3gp']:
                try:
                    await message.answer_video(
                        types.FSInputFile(file_path),
                        caption=f"🎬 {video_title}",
                        supports_streaming=True
                    )
                except Exception as send_err:
                    if "Request Entity Too Large" in str(send_err):
                        await message.answer_document(
                            types.FSInputFile(file_path),
                            caption=f"📁 {video_title}\n(Sent as document due to size)"
                        )
                    else:
                        raise send_err
            else:
                await message.answer_document(
                    types.FSInputFile(file_path),
                    caption=f"📁 {video_title}"
                )
        except Exception as send_final_err:
            logger.error(f"Failed to send media: {send_final_err}")
            await message.answer(f"❌ Failed to send the file. It might be too large or corrupted.")

        # Delete downloading message
        try:
            await downloading_msg.delete()
        except:
            pass
        
        # Clean up file
        await downloader.cleanup_file(file_path)
        
    except Exception as e:
        logger.error(f"Error in process_video_download: {e}")
        if "Request Entity Too Large" in str(e):
            await message.answer("❌ The file is too large to be sent via Telegram (50MB limit).")
        else:
            await message.answer(f"❌ Error: {str(e)[:100]}")


async def process_download_request(message: types.Message, query: str):
    """Process a download request (legacy function for inline mode)"""
    # Send searching message
    searching_msg = await message.answer("🔍 Searching for video...")
    
    try:
        # Search for video info (single result for inline mode)
        videos = await downloader.search_videos(query, max_results=1)
        
        if not videos:
            await searching_msg.edit_text("❌ No video found for your search query.")
            return
        
        video_info = videos[0]
        video_url = video_info['url']
        
        await searching_msg.edit_text(
            f"🎥 Found: <b>{video_info['title']}</b>\n"
            f"⬇️ Downloading..."
        )
        
        # Download video
        await process_video_download(message, video_url, platform="YouTube")
        await searching_msg.delete()
        
    except Exception as e:
        logger.error(f"Error in process_download_request: {e}")
        await searching_msg.edit_text("❌ An error occurred while processing your request.")


@dp.inline_query()
async def handle_inline_query(inline_query: types.InlineQuery):
    """Handle inline queries"""
    query = inline_query.query.strip()
    
    if not query:
        return
    
    try:
        # Search for videos (multiple results for inline mode)
        videos = await downloader.search_videos(query, max_results=5)
        
        if not videos:
            # Return "no results" message
            result = InlineQueryResultArticle(
                id="no_results",
                title="No results found",
                description=f"No videos found for: {query}",
                input_message_content=InputTextMessageContent(
                    message_text=f"❌ No videos found for: {query}"
                )
            )
            await inline_query.answer([result], cache_time=1)
            return
        
        # Create inline results
        results = []
        for i, video in enumerate(videos):
            # Truncate title if too long
            title = video['title'][:60] + "..." if len(video['title']) > 60 else video['title']
            duration = f"{video['duration']//60}:{video['duration']%60:02d}" if video['duration'] else "Unknown"
            
            result = InlineQueryResultArticle(
                id=f"{video['id']}_{i}",
                title=title,
                description=f"Duration: {duration} • Uploader: {video['uploader']}",
                thumbnail_url=video['thumbnail'],
                input_message_content=InputTextMessageContent(
                    message_text=f"https://www.youtube.com/watch?v={video['id']}"
                )
            )
            results.append(result)
        
        await inline_query.answer(results, cache_time=300)
        
    except Exception as e:
        logger.error(f"Error in inline query: {e}")
        # Return error message
        result = InlineQueryResultArticle(
            id="error",
            title="Search error",
            description="An error occurred while searching",
            input_message_content=InputTextMessageContent(
                message_text="❌ An error occurred while searching for videos"
            )
        )
        await inline_query.answer([result], cache_time=1)


async def handle_health(request):
    """Simple health-check endpoint for Render"""
    return web.Response(text="OK", status=200)

async def start_health_server():
    """Start a lightweight web server for Render health checks"""
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    logger.info(f"Starting health check server on port {port}...")
    await site.start()

async def main():
    """Main function to start the bot"""
    logger.info("Starting YouTube Downloader Bot...")
    
    # Clean up downloads directory on startup
    for file in DOWNLOADS_DIR.glob("*"):
        if file.is_file():
            try:
                file.unlink()
                logger.info(f"Cleaned up old file: {file}")
            except Exception as e:
                logger.error(f"Error cleaning up {file}: {e}")
    
    # Start health check server if on Render or configured
    if os.getenv("PORT") or os.getenv("RENDER"):
        asyncio.create_task(start_health_server())

    # Start polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
