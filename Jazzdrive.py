import re
import os
import logging
import tempfile
import glob
import requests
import traceback
import subprocess
import asyncio
import aiohttp
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters,
    ContextTypes, ConversationHandler
)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, SessionNotCreatedException
import yt_dlp
import time

# Configuration
TOKEN = "8112251652:AAHQ7msdI8zTC6DjzdkPhwmclZmreN_taj8"
COOKIES_FILE = "cookies.txt"
YT_COOKIES_DRIVE_URL = "https://drive.google.com/uc?export=download&id=13iX8xpx47W3PAedGyhGpF5CxZRFz4uaF"

# Web Server Configuration
WEB_PORT = 8000
PING_INTERVAL = 25
HEALTH_CHECK_ENDPOINT = "/health"

# Conversation states
LOGIN_PHONE, LOGIN_OTP, YT_QUALITY = range(3)

# Global variables
user_data = {}
YT_COOKIES = None
driver = None
runner = None
site = None

# Initialize logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def initialize_driver():
    """Initialize Chrome WebDriver with unique user data directory"""
    global driver
    
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    
    # Add unique user data directory to prevent conflicts
    user_data_dir = f"/tmp/chrome_user_data_{int(time.time())}"
    chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
    
    # Additional options for stability
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # Set binary location
    chrome_options.binary_location = os.environ.get('GOOGLE_CHROME_BIN', '/usr/bin/google-chrome')
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def download_file(url, save_path=None):
    """Download file with proper filename handling"""
    print(f"[DEBUG] Downloading file: {url}")
    try:
        with requests.get(url, stream=True) as response:
            response.raise_for_status()
            
            # Get filename from Content-Disposition or from URL
            if not save_path:
                if 'content-disposition' in response.headers:
                    filename = re.findall('filename="?(.+)"?', response.headers['content-disposition'])[0]
                else:
                    filename = os.path.basename(url.split('?')[0])
                save_path = os.path.join(tempfile.gettempdir(), filename)
            
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            print(f"[DEBUG] Download successful: {save_path}")
            return save_path
            
    except Exception as e:
        logger.error(f"Failed to download {url}: {str(e)}")
        print("[ERROR]", e)
        return None

def ensure_cookies():
    """Ensure cookies file exists"""
    global YT_COOKIES
    if not os.path.exists(COOKIES_FILE):
        download_file(YT_COOKIES_DRIVE_URL, COOKIES_FILE)
    try:
        with open(COOKIES_FILE, 'r') as f:
            YT_COOKIES = f.read()
        print(f"[DEBUG] Cookies loaded from {COOKIES_FILE}")
    except Exception as e:
        logger.error(f"Error loading cookies: {e}")
        print("[ERROR] Loading cookies:", e)

def has_audio(filename):
    """Check if file has audio"""
    try:
        result = subprocess.run(
            ['ffprobe', '-i', filename, '-show_streams', '-select_streams', 'a', '-loglevel', 'error'],
            capture_output=True,
            text=True
        )
        return "codec_type=audio" in result.stdout
    except Exception as e:
        print("[ERROR] ffprobe audio check failed", e)
        return False

def get_youtube_qualities(url):
    """Get available YouTube qualities"""
    ensure_cookies()
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info or 'formats' not in info:
            return {}
        formats = info['formats']
        out = {}
        # Progressive video+audio
        for fmt in formats:
            if fmt.get('acodec') != 'none' and fmt.get('vcodec') != 'none' and fmt.get('height'):
                label = f"{fmt['height']}p ({fmt.get('format_id')}, {fmt.get('ext')}) [Progressive]"
                out[label] = fmt.get('format_id')
        # DASH video only (require merging with best audio)
        for fmt in formats:
            if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none' and fmt.get('height'):
                label = f"{fmt['height']}p ({fmt.get('format_id')}, {fmt.get('ext')}) [Merged]"
                out[label] = f"{fmt.get('format_id')}+bestaudio[ext=m4a]/bestaudio/best"
        out['BEST (video+audio merged)'] = 'bestvideo+bestaudio/best'
        out['MP3 (audio only)'] = 'bestaudio/best'
        return out

def yt_download_and_merge(url, fmt_string):
    """Download and merge YouTube video"""
    temp_dir = tempfile.mkdtemp()
    try:
        def get_postprocessors(fmt_string):
            if fmt_string == 'bestaudio/best':
                return [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            return [
                {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
                {'key': 'FFmpegMerger'},
                {'key': 'FFmpegMetadata'}
            ]

        ydl_opts = {
            'format': fmt_string,
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',
            'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
            'postprocessors': get_postprocessors(fmt_string),
            'quiet': False,
            'keepvideo': True,
            'noplaylist': True,
            'ignoreerrors': False,
            'retries': 3
        }
        print("[DEBUG][yt-dlp] Options:", ydl_opts)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            print("[DEBUG][yt-dlp] Info:", info)
            
            search_patterns = [
                os.path.join(temp_dir, "*.mp4"),
                os.path.join(temp_dir, "*.mkv"),
                os.path.join(temp_dir, "*.webm"),
                os.path.join(temp_dir, "*.mp3"),
                os.path.join(temp_dir, "*.m4a")
            ]
            
            files = []
            for pattern in search_patterns:
                files.extend(glob.glob(pattern))
                
            if not files:
                print("[ERROR] No completed media file found for upload.")
                return None, None
                
            filename = max(files, key=os.path.getsize)
            
            if fmt_string != 'bestaudio/best' and not has_audio(filename):
                print("[ERROR] Output video is mute. Skipping.")
                return None, None
                
            print(f"[DEBUG] Selected file for delivery: {filename}, size: {os.path.getsize(filename)}")
            return filename, info.get('title', None)
    except Exception as e:
        print("[ERROR] yt-dlp download/merge error:", e)
        traceback.print_exc()
        return None, None
    finally:
        pass

def get_gallery_links():
    """Get links from JazzDrive gallery"""
    try:
        driver.get("https://cloud.jazzdrive.com.pk/#gallery")
        time.sleep(5)
        
        # Scroll to load all items
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
        
        # Find all file links
        file_links = []
        try:
            items = WebDriverWait(driver, 20).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='file'], a[href*='download']"))
            )
            
            for item in items:
                href = item.get_attribute('href')
                text = item.text.strip() or os.path.basename(href)
                if href and not href.startswith('javascript:'):
                    file_links.append(f"{text}: {href}")
        except TimeoutException:
            # Fallback: try to find any links
            items = driver.find_elements(By.TAG_NAME, "a")
            for item in items:
                href = item.get_attribute('href')
                text = item.text.strip() or os.path.basename(href) if href else "Unknown"
                if href and not href.startswith('javascript:'):
                    file_links.append(f"{text}: {href}")
                
        return file_links[:20]  # Limit to 20 links
        
    except Exception as e:
        print(f"[ERROR] Failed to get gallery links: {str(e)}")
        return None

async def health_check(request):
    """Health check endpoint for Koyeb"""
    return web.Response(
        text=f"🤖 JazzDrive Bot is operational | Last active: {datetime.now()}",
        headers={"Content-Type": "text/plain"},
        status=200
    )

async def root_handler(request):
    """Root endpoint handler"""
    return web.Response(
        text="JazzDrive Bot is running",
        status=200
    )

async def self_ping():
    """Keep-alive mechanism for Koyeb"""
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'http://localhost:{WEB_PORT}{HEALTH_CHECK_ENDPOINT}') as resp:
                    status = f"Status: {resp.status}" if resp.status != 200 else "Success"
                    logger.info(f"Keepalive ping {status}")
                    
            with open('/tmp/last_active.txt', 'w') as f:
                f.write(str(datetime.now()))
                
        except Exception as e:
            logger.error(f"Keepalive error: {str(e)}")
        
        await asyncio.sleep(PING_INTERVAL)

async def run_webserver():
    """Run the web server for health checks"""
    global runner, site
    
    app = web.Application()
    app.router.add_get(HEALTH_CHECK_ENDPOINT, health_check)
    app.router.add_get("/", root_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)
    await site.start()
    logger.info(f"Health check server running on port {WEB_PORT}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    await update.message.reply_text(
        "🚀 JazzDrive Upload Bot\n\n"
        "Send me a file link or YouTube URL to upload to JazzDrive\n\n"
        "First, please login with /login command"
    )

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start login process"""
    await update.message.reply_text("Please enter your Jazz mobile number (format: 03XXXXXXXXX):")
    return LOGIN_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive phone number"""
    phone = update.message.text
    if not re.match(r'^03\d{9}$', phone):
        await update.message.reply_text("Invalid format. Please enter in 03XXXXXXXXX format:")
        return LOGIN_PHONE

    user_data['phone'] = phone
    try:
        driver.get("https://jazzdrive.com.pk/")
        time.sleep(2)
        phone_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "msisdn")))
        phone_input.clear()
        phone_input.send_keys(phone)
        continue_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "signinbtn")))
        continue_button.click()
        await update.message.reply_text("OTP request sent. Please enter the 4-digit OTP you received:")
        return LOGIN_OTP
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}. Please try /login again.")
        print("[ERROR] receive_phone:", e)
        return ConversationHandler.END

async def receive_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive OTP"""
    otp = update.message.text
    if not re.match(r'^\d{4}$', otp):
        await update.message.reply_text("Invalid OTP format. Please enter 4 digits:")
        return LOGIN_OTP

    try:
        otp_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "otp")))
        otp_input.clear()
        otp_input.send_keys(otp)
        submit_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "signinbtn")))
        submit_button.click()
        WebDriverWait(driver, 20).until(
            EC.url_contains("highlights"))
        await update.message.reply_text("✅ Login successful! Now you can send me file links or YouTube URLs.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Login failed: {str(e)}. Please try /login again.")
        print("[ERROR] receive_otp:", e)
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel operation"""
    await update.message.reply_text('Operation cancelled.')
    print("[DEBUG] Operation cancelled.")
    return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages"""
    url = update.message.text.strip()
    print(f"[DEBUG] Received message URL: {url}")
    if not hasattr(driver, 'current_url'):
        await update.message.reply_text("Please login first with /login")
        return
    if 'youtube.com' in url or 'youtu.be' in url:
        await handle_youtube(update, context, url)
    else:
        await handle_regular_url(update, context, url)

async def handle_regular_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Handle regular file URLs"""
    message = await update.message.reply_text("⏳ Downloading file...")
    file_path = download_file(url)
    if not file_path:
        await message.edit_text("❌ Failed to download file. Please check the URL and try again.")
        print("[ERROR] File download failed in handle_regular_url.")
        return
    
    try:
        await message.edit_text("📤 Uploading to JazzDrive...")
        if upload_to_jazzdrive(file_path):
            await message.edit_text(f"✅ File uploaded successfully!\n\nFilename: {os.path.basename(file_path)}")
            
            # Get and send gallery links
            gallery_links = get_gallery_links()
            if gallery_links:
                links_text = "📁 Current Gallery Files:\n\n" + "\n".join(gallery_links)
                await update.message.reply_text(links_text)
            else:
                await update.message.reply_text("ℹ️ Could not retrieve gallery links")
        else:
            await message.edit_text("❌ Failed to upload file to JazzDrive.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

def upload_to_jazzdrive(file_path):
    """Upload file to JazzDrive"""
    print(f"[DEBUG] upload_to_jazzdrive called for file_path: {file_path}")
    try:
        driver.get("https://cloud.jazzdrive.com.pk/#gallery")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Gallery')]")))
        
        # Try multiple selectors for upload input
        upload_selectors = [
            (By.ID, "uploadInputField"),
            (By.NAME, "file"),
            (By.CSS_SELECTOR, "input[type='file']"),
            (By.XPATH, "//input[@type='file']")
        ]
        
        upload_input = None
        for selector in upload_selectors:
            try:
                upload_input = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(selector))
                break
            except:
                continue
        
        if not upload_input:
            raise Exception("Could not find upload input field")
            
        if not upload_input.is_displayed() or not upload_input.is_enabled():
            driver.execute_script("arguments[0].style.display = 'block';", upload_input)
            time.sleep(1)
            
        upload_input.send_keys(os.path.abspath(file_path))
        time.sleep(10)  # Wait longer for upload
        
        # Check for success indicators
        success_indicators = [
            "upload completed",
            "upload successful",
            "upload finished",
            "100%"
        ]
        
        for indicator in success_indicators:
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, f"//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{indicator}')]")))
                print(f"[DEBUG] upload_to_jazzdrive success for {file_path}")
                return True
            except:
                continue
                
        # If no success indicator found, assume success after waiting
        time.sleep(5)
        print(f"[DEBUG] upload_to_jazzdrive (assume success) for {file_path}")
        return True
        
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        print("[ERROR] upload_to_jazzdrive:", e)
        return False

async def handle_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    """Handle YouTube URLs"""
    print(f"[DEBUG] handle_youtube called for URL: {url}")
    qualities = get_youtube_qualities(url)
    if not qualities:
        await update.message.reply_text("No available video qualities found.")
        print("[ERROR] No available video qualities found in handle_youtube.")
        return
    keyboard = [[InlineKeyboardButton(label, callback_data=f"yt_{label}")] for label in qualities]
    context.user_data['yt_url'] = url
    context.user_data['yt_qualities'] = qualities
    reply_markup = InlineKeyboardMarkup(keyboard)
    print(f"[DEBUG] handle_youtube sending buttons: {list(qualities.keys())}")
    await update.message.reply_text("Choose video quality:", reply_markup=reply_markup)
    return YT_QUALITY

async def youtube_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube quality selection"""
    query = update.callback_query
    await query.answer()
    label = query.data.replace("yt_", "")
    url = context.user_data['yt_url']
    qualities = context.user_data['yt_qualities']
    fmt_string = qualities.get(label, 'bestvideo+bestaudio/best')
    msg = await query.edit_message_text(f"⏳ Downloading YouTube video/audio ({label})...")
    
    try:
        file_path, title = yt_download_and_merge(url, fmt_string)
        if not file_path:
            await msg.edit_text("❌ Could not find a completed media file for upload!")
            return
            
        await msg.edit_text("📤 Uploading to JazzDrive...")
        upload_success = upload_to_jazzdrive(file_path)
        print(f"[DEBUG] upload_to_jazzdrive returned: {upload_success}")
        
        if upload_success:
            await msg.edit_text(f"✅ YouTube video/audio uploaded successfully!\n\nTitle: {title}\nQuality: {label}")
            
            # Get and send gallery links
            gallery_links = get_gallery_links()
            if gallery_links:
                links_text = "📁 Current Gallery Files:\n\n" + "\n".join(gallery_links)
                await query.message.reply_text(links_text)
            else:
                await query.message.reply_text("ℹ️ Could not retrieve gallery links")
        else:
            await msg.edit_text(f"❌ Failed to upload video/audio to JazzDrive.\nTitle: {title}")
            
    except Exception as e:
        print("[EXCEPTION]", repr(e))
        traceback.print_exc()
        await msg.edit_text(f"❌ Error processing YouTube video:\n{str(e)}")
        
    finally:
        if 'file_path' in locals() and file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"[DEBUG] Temp file {file_path} removed.")
            except Exception as ex:
                print("[ERROR] Removing file_path in finally block:", ex)
                
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    error = context.error
    tb_list = traceback.format_exception(type(error), error, error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error(f"Exception occurred:\n{tb_string}")
    
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An error occurred. Please try again."
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

async def run_bot():
    """Run the Telegram bot with web server"""
    global driver
    
    # Initialize driver
    driver = initialize_driver()
    
    application = Application.builder().token(TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    
    # Login conversation handler
    login_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login)],
        states={
            LOGIN_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            LOGIN_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_otp)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(login_handler)
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(youtube_quality_callback, pattern="^yt_"))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start components
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Run web server and keepalive
    await run_webserver()
    asyncio.create_task(self_ping())
    
    logger.info("Bot started successfully!")
    
    # Keep running
    while True:
        await asyncio.sleep(3600)

async def main():
    """Main entry point"""
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            ensure_cookies()
            await run_bot()
            break
        except Exception as e:
            logger.error(f"Error (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                # Clean up any existing driver
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
                    driver = None
            else:
                logger.error("Max retries exceeded")
                raise
    
    # Cleanup
    logger.info("Starting cleanup process...")
    
    try:
        if site:
            await site.stop()
    except:
        pass
    
    try:
        if runner:
            await runner.cleanup()
    except:
        pass
    
    try:
        if driver:
            driver.quit()
    except:
        pass
        
    logger.info("Cleanup completed")

if __name__ == "__main__":
    asyncio.run(main())
