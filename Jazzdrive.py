import os
import logging
import asyncio
import aiohttp
from datetime import datetime
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException
import tempfile
import time

# Configuration
TOKEN = "8112251652:AAHQ7msdI8zTC6DjzdkPhwmclZmreN_taj8"

# Web Server Configuration
WEB_PORT = 8000
PING_INTERVAL = 25
HEALTH_CHECK_ENDPOINT = "/health"

# Global variables
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
    """Initialize Chrome WebDriver using webdriver-manager"""
    global driver
    try:
        chrome_options = Options()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-gpu")
        
        # Additional options for stability
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--no-default-browser-check")
        chrome_options.add_argument("--disable-extensions")
        
        logger.info("Initializing Chrome WebDriver with webdriver-manager...")
        
        # Use webdriver-manager to automatically handle ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        logger.info("WebDriver initialized successfully!")
        return driver
        
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {str(e)}")
        raise

async def health_check(request):
    """Health check endpoint for Koyeb"""
    return web.Response(
        text=f"🤖 Basic Bot is operational | Last active: {datetime.now()}",
        headers={"Content-Type": "text/plain"},
        status=200
    )

async def root_handler(request):
    """Root endpoint handler"""
    return web.Response(
        text="Basic Bot is running",
        status=200
    )

async def self_ping():
    """Keep-alive mechanism for Koyeb"""
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'http://localhost:{WEB_PORT}{HEALTH_CHECK_ENDPOINT}') as resp:
                    if resp.status == 200:
                        logger.info("Keepalive ping successful")
                    else:
                        logger.warning(f"Keepalive ping status: {resp.status}")
                    
            with open('/tmp/last_active.txt', 'w') as f:
                f.write(str(datetime.now()))
                
        except Exception as e:
            logger.error(f"Keepalive error: {str(e)}")
        
        await asyncio.sleep(PING_INTERVAL)

async def run_webserver():
    """Run the web server for health checks"""
    app = web.Application()
    app.router.add_get(HEALTH_CHECK_ENDPOINT, health_check)
    app.router.add_get("/", root_handler)
    
    global runner, site
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)
    await site.start()
    logger.info(f"Health check server running on port {WEB_PORT}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    await update.message.reply_text(
        "🚀 Basic WebDriver Bot\n\n"
        "Send /test to test the WebDriver functionality"
    )

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test WebDriver functionality"""
    global driver
    message = await update.message.reply_text("⏳ Testing WebDriver...")
    
    try:
        # Initialize driver if not already done
        if driver is None:
            await message.edit_text("🚀 Initializing WebDriver...")
            driver = initialize_driver()
            await message.edit_text("✅ WebDriver initialized successfully!")
            await asyncio.sleep(1)
        
        # Open Google.com
        await message.edit_text("🌐 Opening Google.com...")
        driver.get("https://www.google.com")
        
        # Wait for page to load
        await asyncio.sleep(2)
        
        # Take screenshot
        await message.edit_text("📸 Taking screenshot...")
        screenshot_path = os.path.join(tempfile.gettempdir(), f"google_screenshot_{int(time.time())}.png")
        driver.save_screenshot(screenshot_path)
        
        # Send screenshot to user
        await message.edit_text("📤 Sending screenshot...")
        with open(screenshot_path, 'rb') as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption="✅ Screenshot of Google.com taken successfully!"
            )
        
        # Clean up
        os.remove(screenshot_path)
        await message.edit_text("✅ Test completed successfully!")
        
    except WebDriverException as e:
        error_msg = f"❌ WebDriver Error: {str(e)}"
        logger.error(error_msg)
        await message.edit_text(error_msg)
        # Reset driver on error
        if driver:
            try:
                driver.quit()
            except:
                pass
            driver = None
        
    except Exception as e:
        error_msg = f"❌ Unexpected Error: {str(e)}"
        logger.error(error_msg)
        await message.edit_text(error_msg)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    error = context.error
    logger.error(f"Exception occurred: {error}")
    
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
    application = Application.builder().token(TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_command))
    
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
    try:
        await run_bot()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
    finally:
        logger.info("Starting cleanup process...")
        
        global runner, site, driver
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
