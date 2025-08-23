import os
import logging
import asyncio
import tempfile
import subprocess
from datetime import datetime
from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, CallbackContext, TypeHandler
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import WebDriverException

# --- Configuration ---
# It's best practice to use environment variables for sensitive data
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_FALLBACK_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
# This should be your Koyeb app URL, e.g., "https://my-bot-app-my-org.koyeb.app"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- WebDriver Management ---
def initialize_driver():
    """
    Initializes a new Selenium WebDriver instance for Chrome.
    This function should be called only when needed to avoid stale sessions.
    """
    logger.info("Initializing new WebDriver instance...")
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--single-process") # Important for containerized environments

    # Path to chromedriver is usually /usr/local/bin/chromedriver in this Docker setup
    service = ChromeService(executable_path="/usr/local/bin/chromedriver")
    
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        logger.info("WebDriver initialized successfully!")
        return driver
    except WebDriverException as e:
        logger.error(f"Failed to initialize WebDriver: {e}")
        # Log detailed stacktrace for debugging
        logger.exception("WebDriver initialization stacktrace:")
        raise  # Re-raise the exception to be caught by the command handler

# --- Telegram Command Handlers ---
async def start_command(update: Update, context: CallbackContext):
    """Handler for the /start command."""
    await update.message.reply_text(
        "🚀 WebDriver Bot is ready!\n\n"
        "Send /test to run a Selenium test.\n"
        "Send /debug to check system and environment info."
    )

async def debug_command(update: Update, context: CallbackContext):
    """Handler for the /debug command to check system status."""
    try:
        chrome_path = subprocess.getoutput('which google-chrome-stable')
        chromedriver_path = subprocess.getoutput('which chromedriver')
        chrome_version = subprocess.getoutput('google-chrome-stable --version')
        
        debug_info = (
            f"🤖 **System & Bot Debug Info**\n\n"
            f"**Webhook URL:** `{WEBHOOK_URL}`\n"
            f"**Listening Port:** `{PORT}`\n\n"
            f"**Chrome Path:** `{chrome_path}`\n"
            f"**ChromeDriver Path:** `{chromedriver_path}`\n"
            f"**Chrome Version:** `{chrome_version}`\n\n"
            f"**Python Version:** `{os.sys.version}`"
        )
        await update.message.reply_text(debug_info, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Debug command failed: {e}")
        await update.message.reply_text(f"❌ Error during debug check: {e}")

async def test_command(update: Update, context: CallbackContext):
    """
    Handler for the /test command. Initializes WebDriver, runs a test, and quits.
    """
    message = await update.message.reply_text("⏳ Test started. Initializing WebDriver...")
    driver = None  # Ensure driver is defined in this scope
    try:
        driver = initialize_driver()
        await message.edit_text("🌐 WebDriver initialized. Opening Google.com...")
        
        driver.get("https://www.google.com")
        await asyncio.sleep(2)  # Allow time for the page to render

        await message.edit_text(f"📸 Page '{driver.title}' loaded. Taking screenshot...")
        
        # Use a temporary file for the screenshot
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            screenshot_path = tmp_file.name
            driver.save_screenshot(screenshot_path)

        await message.edit_text("📤 Sending screenshot...")
        
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open(screenshot_path, 'rb'),
            caption=f"✅ Test successful! Screenshot of '{driver.title}'."
        )
        
        # Clean up the sent message and file
        await message.delete()
        os.remove(screenshot_path)

    except Exception as e:
        error_message = f"❌ An error occurred during the test: {e}"
        logger.error(error_message, exc_info=True)
        await message.edit_text(error_message)
    finally:
        if driver:
            logger.info("Quitting WebDriver instance.")
            driver.quit()

# --- Web Server for Webhook and Health Checks ---
async def health_check(request: web.Request) -> web.Response:
    """AIOHTTP handler for Koyeb's health checks."""
    return web.Response(text="OK", status=200)

async def telegram_webhook(request: web.Request) -> web.Response:
    """AIOHTTP handler for receiving updates from Telegram."""
    application = request.app['bot_app']
    update = Update.de_json(await request.json(), application.bot)
    await application.process_update(update)
    return web.Response(status=200)

async def main():
    """Main function to set up and run the bot and web server."""
    if not WEBHOOK_URL:
        logger.error("FATAL: WEBHOOK_URL environment variable not set!")
        return
    if "YOUR_FALLBACK_TOKEN" in TOKEN:
        logger.error("FATAL: TELEGRAM_BOT_TOKEN not set correctly!")
        return

    # Initialize the Telegram bot application
    bot = Bot(token=TOKEN)
    application = Application.builder().bot(bot).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("debug", debug_command))

    # Set up the webhook
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/telegram")
    logger.info(f"Webhook set to {WEBHOOK_URL}/telegram")

    # Set up the AIOHTTP web server
    webapp = web.Application()
    webapp['bot_app'] = application
    webapp.router.add_post("/telegram", telegram_webhook)
    webapp.router.add_get("/health", health_check) # For Koyeb health checks

    # Run the web server
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    
    logger.info(f"Starting web server on port {PORT}...")
    await site.start()

    # Keep the application running
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
    except Exception as e:
        logger.critical(f"Application failed to run: {e}", exc_info=True)

