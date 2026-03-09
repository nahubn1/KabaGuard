import os
import logging
import ssl
import warnings
from dotenv import load_dotenv

# Load environment variables FIRST before importing telegram or httpx
load_dotenv()

# SSL bypass for corporate networks - must be done BEFORE importing telegram
if os.getenv("DISABLE_SSL_VERIFY", "0").lower() in ("1", "true", "yes"):
    import httpx
    warnings.filterwarnings('ignore')
    
    # Monkey-patch httpx to always use verify=False
    _original_init = httpx.AsyncClient.__init__
    
    def _patched_init(self, *args, **kwargs):
        kwargs['verify'] = False
        return _original_init(self, *args, **kwargs)
    
    httpx.AsyncClient.__init__ = _patched_init
    print("⚠️  SSL verification is DISABLED globally - not secure for production!")

from telegram import Update
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .utils import EAT
from .database import Database
from .scheduler import check_all_users_attendance
from .handlers import (
    start_command, help_command, status_command, check_command, test_command,
    register_start, ask_start_time, ask_end_time, ask_working_days, 
    confirm_registration, save_registration, cancel_registration,
    ASK_KABA_ID, ASK_START_TIME, ASK_END_TIME, ASK_WORKING_DAYS, CONFIRM
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Re-initialize DB using the same path config (to ensure it resolves to root/kabaguard.db if matching)
# Actually, dotenv loads relative to cwd, so it should still use the project root kabaguard.db
db = Database(os.getenv("DATABASE_PATH", "kabaguard.db"))

async def post_init(application: Application) -> None:
    """Initialize database and scheduler after application start."""
    await db.init_db()
    logger.info("Database initialized")
    
    # Setup and start scheduler (event loop is now running)
    scheduler = AsyncIOScheduler(timezone=EAT)
    scheduler.add_job(
        check_all_users_attendance,
        trigger='interval',
        minutes=5,
        id='attendance_checker',
        name='Check all users attendance'
    )
    scheduler.start()
    logger.info("Scheduler started (runs every 5 minutes)")
    
    # Store scheduler in application context for graceful shutdown
    application.bot_data['scheduler'] = scheduler

def main() -> None:
    """Start the bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables!")
    
    # Retrieve the custom Proxy URL from the environment (for corporate firewall bypass)
    custom_api_url = os.getenv("TELEGRAM_API_URL")
    
    # Create application builder
    builder = Application.builder().token(token).post_init(post_init)
    
    # If a custom Gateway URL is provided, instruct the bot to use it
    if custom_api_url:
        # Ensure it ends with /bot so the token is appended correctly
        if not custom_api_url.endswith("/bot"):
            custom_api_url = f"{custom_api_url.rstrip('/')}/bot"
            
        logger.info(f"Using Custom Telegram Relay Gateway: {custom_api_url}")
        builder.base_url(custom_api_url)
        
    application = builder.build()
    
    # Register conversation handler for registration
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler("register", register_start)],
        states={
            ASK_KABA_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start_time)],
            ASK_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_end_time)],
            ASK_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_working_days)],
            ASK_WORKING_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_registration)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_registration)],
        },
        fallbacks=[CommandHandler("cancel", cancel_registration)],
    )
    
    application.add_handler(registration_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("test", test_command))
    
    # Start bot (scheduler will be initialized in post_init)
    logger.info("KabaGuard bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
