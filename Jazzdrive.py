import re
import os
import logging
import tempfile
import glob
import requests
import traceback
import subprocess
import asyncio
from datetime import datetime
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
from selenium.common.exceptions import TimeoutException
import yt_dlp
import time

# Configuration
TOKEN = "8112251652:AAHQ7msdI8zTC6DjzdkPhwmclZmreN_taj8"
COOKIES_FILE = "cookies.txt"
YT_COOKIES_DRIVE_URL = "https://drive.google.com/uc?export=download&id=13iX8xpx47W3PAedGyhGpF5CxZRFz4uaF"

LOGIN_PHONE, LOGIN_OTP, YT_QUALITY = range(3)
user_data = {}
YT_COOKIES = None
driver = None

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def initialize_driver():
    """Initialize headless Chrome WebDriver for Linux cloud/CI environments."""
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    # If using chromium:
    # chrome_options.binary_location = "/usr/bin/chromium-browser"
    # If using google-chrome:
    chrome_options.binary_location = "/usr/bin/google-chrome"
    driver = webdriver.Chrome(options=chrome_options)
    logger.info("Chrome driver initialized.")
    return driver

def download_file(url, save_path=None):
    try:
        with requests.get(url, stream=True) as response:
            response.raise_for_status()
            if not save_path:
                filename = os.path.basename(url.split('?')[0])
                save_path = os.path.join(tempfile.gettempdir(), filename)
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return save_path
    except Exception as e:
        logger.error(f"Failed to download {url}: {str(e)}")
        return None

def ensure_cookies():
    global YT_COOKIES
    if not os.path.exists(COOKIES_FILE):
        download_file(YT_COOKIES_DRIVE_URL, COOKIES_FILE)
    try:
        with open(COOKIES_FILE, 'r') as f:
            YT_COOKIES = f.read()
    except Exception as e:
        logger.error(f"Error loading cookies: {e}")

def has_audio(filename):
    try:
        result = subprocess.run(
            ['ffprobe', '-i', filename, '-show_streams', '-select_streams', 'a', '-loglevel', 'error'],
            capture_output=True, text=True
        )
        return "codec_type=audio" in result.stdout
    except Exception:
        return False

def get_youtube_qualities(url):
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
        for fmt in formats:
            if fmt.get('acodec') != 'none' and fmt.get('vcodec') != 'none' and fmt.get('height'):
                label = f"{fmt['height']}p ({fmt.get('format_id')}, {fmt.get('ext')})"
                out[label] = fmt.get('format_id')
        for fmt in formats:
            if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none' and fmt.get('height'):
                label = f"{fmt['height']}p ({fmt.get('format_id')}, {fmt.get('ext')}) [Merged]"
                out[label] = f"{fmt.get('format_id')}+bestaudio[ext=m4a]/bestaudio/best"
        out['BEST (video+audio merged)'] = 'bestvideo+bestaudio/best'
        out['MP3 (audio only)'] = 'bestaudio/best'
        return out

def yt_download_and_merge(url, fmt_string):
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
                {'key': 'FFmpegMerger'}, {'key': 'FFmpegMetadata'}
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
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            files = []
            for ext in ["*.mp4", "*.mkv", "*.webm", "*.mp3", "*.m4a"]:
                files.extend(glob.glob(os.path.join(temp_dir, ext)))
            if not files:
                return None, None
            filename = max(files, key=os.path.getsize)
            if fmt_string != 'bestaudio/best' and not has_audio(filename):
                return None, None
            return filename, info.get('title', None)
    except Exception:
        return None, None

def get_gallery_links():
    try:
        driver.get("https://cloud.jazzdrive.com.pk/#gallery")
        time.sleep(5)
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
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
            items = driver.find_elements(By.TAG_NAME, "a")
            for item in items:
                href = item.get_attribute('href')
                text = item.text.strip() or os.path.basename(href) if href else "Unknown"
                if href and not href.startswith('javascript:'):
                    file_links.append(f"{text}: {href}")
        return file_links[:20]
    except Exception as e:
        print(f"Gallery error: {str(e)}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 JazzDrive Upload Bot\nSend me a file link or YouTube URL to upload to JazzDrive.\nUse /login first."
    )

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enter your Jazz mobile number (03XXXXXXXXX):")
    return LOGIN_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text
    if not re.match(r'^03\d{9}$', phone):
        await update.message.reply_text("Invalid format. Try 03XXXXXXXXX.")
        return LOGIN_PHONE
    user_data['phone'] = phone
    try:
        driver.get("https://jazzdrive.com.pk/")
        time.sleep(2)
        phone_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "msisdn")))
        phone_input.clear()
        phone_input.send_keys(phone)
        continue_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "signinbtn")))
        continue_btn.click()
        await update.message.reply_text("OTP request sent. Please enter 4-digit OTP:")
        return LOGIN_OTP
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}. Try /login again.")
        return ConversationHandler.END

async def receive_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text
    if not re.match(r'^\d{4}$', otp):
        await update.message.reply_text("Invalid OTP. Enter 4 digits.")
        return LOGIN_OTP
    try:
        otp_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "otp")))
        otp_input.clear()
        otp_input.send_keys(otp)
        submit_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "signinbtn")))
        submit_btn.click()
        WebDriverWait(driver, 20).until(EC.url_contains("highlights"))
        await update.message.reply_text("✅ Login successful! Send file/YouTube links.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"Login failed: {str(e)}. Try /login again.")
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Cancelled.')
    return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not hasattr(driver, 'current_url'):
        await update.message.reply_text("Login first with /login.")
        return
    if 'youtube.com' in url or 'youtu.be' in url:
        await handle_youtube(update, context, url)
    else:
        await handle_regular_url(update, context, url)

async def handle_regular_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    message = await update.message.reply_text("⏳ Downloading...")
    file_path = download_file(url)
    if not file_path:
        await message.edit_text("❌ Download failed.")
        return
    try:
        await message.edit_text("📤 Uploading to JazzDrive...")
        if upload_to_jazzdrive(file_path):
            await message.edit_text(f"✅ Uploaded!\nFilename: {os.path.basename(file_path)}")
            gallery_links = get_gallery_links()
            if gallery_links:
                links_text = "📁 Gallery Files:\n" + "\n".join(gallery_links)
                await update.message.reply_text(links_text)
            else:
                await update.message.reply_text("Could not retrieve gallery links.")
        else:
            await message.edit_text("❌ Upload to JazzDrive failed.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

def upload_to_jazzdrive(file_path):
    try:
        driver.get("https://cloud.jazzdrive.com.pk/#gallery")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Gallery')]")))
        upload_selectors = [
            (By.ID, "uploadInputField"),
            (By.NAME, "file"),
            (By.CSS_SELECTOR, "input[type='file']"),
            (By.XPATH, "//input[@type='file']")
        ]
        upload_input = None
        for selector in upload_selectors:
            try:
                upload_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located(selector))
                break
            except:
                continue
        if not upload_input:
            raise Exception("No upload input field found")
        if not upload_input.is_displayed() or not upload_input.is_enabled():
            driver.execute_script("arguments[0].style.display = 'block';", upload_input)
            time.sleep(1)
        upload_input.send_keys(os.path.abspath(file_path))
        time.sleep(10)
        success_indicators = ["upload completed", "upload successful", "upload finished", "100%"]
        for indicator in success_indicators:
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, f"//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{indicator}')]")))
                return True
            except:
                continue
        time.sleep(5)
        return True
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        return False

async def handle_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    qualities = get_youtube_qualities(url)
    if not qualities:
        await update.message.reply_text("No qualities found.")
        return
    keyboard = [[InlineKeyboardButton(label, callback_data=f"yt_{label}")] for label in qualities]
    context.user_data['yt_url'] = url
    context.user_data['yt_qualities'] = qualities
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose video quality:", reply_markup=reply_markup)
    return YT_QUALITY

async def youtube_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    label = query.data.replace("yt_", "")
    url = context.user_data['yt_url']
    qualities = context.user_data['yt_qualities']
    fmt_string = qualities.get(label, 'bestvideo+bestaudio/best')
    msg = await query.edit_message_text(f"⏳ Downloading ({label})...")
    try:
        file_path, title = yt_download_and_merge(url, fmt_string)
        if not file_path:
            await msg.edit_text("❌ No media file for upload!")
            return
        await msg.edit_text("📤 Uploading to JazzDrive...")
        upload_success = upload_to_jazzdrive(file_path)
        if upload_success:
            await msg.edit_text(f"✅ Uploaded!\nTitle: {title}\nQuality: {label}")
            gallery_links = get_gallery_links()
            if gallery_links:
                links_text = "📁 Gallery Files:\n" + "\n".join(gallery_links)
                await query.message.reply_text(links_text)
            else:
                await query.message.reply_text("Could not retrieve gallery links.")
        else:
            await msg.edit_text(f"❌ Upload failed.\nTitle: {title}")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        if 'file_path' in locals() and file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    global driver
    driver = initialize_driver()
    ensure_cookies()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    login_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login)],
        states={
            LOGIN_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            LOGIN_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_otp)],
        }, fallbacks=[CommandHandler('cancel', cancel)],
    )
    app.add_handler(login_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(youtube_quality_callback, pattern="^yt_"))
    app.add_error_handler(error_handler)
    logger.info("Bot started successfully!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    while True:
        await asyncio.sleep(3600)

def main():
    try:
        asyncio.run(run_bot())
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        traceback.print_exc()
    finally:
        if driver:
            driver.quit()
        logger.info("Cleanup completed")

if __name__ == "__main__":
    main()
