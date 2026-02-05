"""
KabaGuard - Main Telegram Bot Application
Attendance tracking bot with DB-first optimization and EAT/UTC+3 timezone support.
"""

import os
import logging
from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo
from typing import Optional

import aiohttp
import holidays
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import telegram.request
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import Database
from scraper import check_attendance_async, AttendanceStatus

# Check if SSL verification should be disabled (for corporate networks)
import ssl
import certifi

# Load environment variables FIRST
load_dotenv()

# SSL bypass for corporate networks - must be done BEFORE importing telegram
if os.getenv("DISABLE_SSL_VERIFY", "0").lower() in ("1", "true", "yes"):
    import ssl
    import httpx
    import warnings
    warnings.filterwarnings('ignore')
    
    # Monkey-patch httpx to always use verify=False
    _original_init = httpx.AsyncClient.__init__
    
    def _patched_init(self, *args, **kwargs):
        kwargs['verify'] = False
        return _original_init(self, *args, **kwargs)
    
    httpx.AsyncClient.__init__ = _patched_init
    print("⚠️  SSL verification is DISABLED globally - not secure for production!")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Timezone configuration for Ethiopia (EAT/UTC+3)
EAT = ZoneInfo("Africa/Addis_Ababa")

# Ethiopian holidays
ethiopian_holidays = holidays.Ethiopia()

# Conversation states
ASK_KABA_ID, ASK_START_TIME, ASK_END_TIME, ASK_WORKING_DAYS, CONFIRM = range(5)

# Initialize database
db = Database(os.getenv("DATABASE_PATH", "kabaguard.db"))


# ==================== UTILITY FUNCTIONS ====================

def get_current_time_eat() -> datetime:
    """Get current time in Ethiopian timezone (EAT/UTC+3)."""
    return datetime.now(EAT)


def parse_time(time_str: str) -> Optional[time]:
    """Parse time string in HH:MM format."""
    try:
        return datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return None


def is_ethiopian_holiday(check_date: date) -> bool:
    """Check if a date is an Ethiopian holiday."""
    return check_date in ethiopian_holidays


# ==================== COMMAND HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Welcome to *KabaGuard*!\n\n"
        "I help you track your attendance on the company portal.\n\n"
        "🔹 Use /register to set up your schedule\n"
        "🔹 Use /status to view your current settings\n"
        "🔹 Use /help for more information\n\n"
        "I'll automatically check your attendance during your shift hours "
        "and notify you if you forget to clock in or out!",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(
        "*KabaGuard Commands:*\n\n"
        "/start - Welcome message\n"
        "/register - Register or update your schedule\n"
        "/status - View your current settings\n"
        "/check YYYY-MM-DD - Test scraper for a specific date\n"
        "/test HH:MM - Simulate scheduler at a specific time\n"
        "/help - Show this help message\n\n"
        "*How it works:*\n"
        "After registration, I'll check the company portal during your shift hours. "
        "If you forget to clock in/out, I'll send you an alert!",
        parse_mode="Markdown"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    user = await db.get_user(update.effective_user.id)
    
    if not user:
        await update.message.reply_text(
            "❌ You haven't registered yet!\n\n"
            "Use /register to set up your schedule."
        )
        return
    
    # Parse working days
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    working_day_indices = [int(d) for d in user['working_days'].split(',')]
    working_day_names = [day_names[i] for i in working_day_indices]
    
    await update.message.reply_text(
        f"✅ *Your Current Settings:*\n\n"
        f"🆔 Kaba ID: `{user['kaba_id']}`\n"
        f"🕐 Shift Start: {user['start_time']}\n"
        f"🕔 Shift End: {user['end_time']}\n"
        f"📅 Working Days: {', '.join(working_day_names)}\n\n"
        f"Use /register to update your settings.",
        parse_mode="Markdown"
    )


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /check command - test scraper with a specific date."""
    user = await db.get_user(update.effective_user.id)
    
    if not user:
        await update.message.reply_text(
            "❌ You haven't registered yet!\n\n"
            "Use /register to set up your Kaba ID first."
        )
        return
    
    # Parse date from command arguments
    if not context.args:
        await update.message.reply_text(
            "🔍 *Check Attendance Status*\n\n"
            "Usage: `/check YYYY-MM-DD`\n\n"
            "Example: `/check 2026-02-04`\n\n"
            "This will scrape the portal and show your clock in/out status for that date.",
            parse_mode="Markdown"
        )
        return
    
    # Parse the date
    try:
        from datetime import datetime
        date_str = context.args[0]
        check_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid date format!\n\n"
            "Please use YYYY-MM-DD format (e.g., 2026-02-04)"
        )
        return
    
    # Show processing message
    await update.message.reply_text(
        f"🔄 Checking attendance for {check_date.strftime('%B %d, %Y')}...\n\n"
        f"Scraping portal with Kaba ID: `{user['kaba_id']}`",
        parse_mode="Markdown"
    )
    
    # Scrape the portal
    portal_url = os.getenv("PORTAL_URL", "https://example.com/attendance")
    
    async with aiohttp.ClientSession() as session:
        try:
            status = await check_attendance_async(session, user['kaba_id'], check_date, portal_url)
            
            # Format the result
            if status == AttendanceStatus.CLOCKED_OUT:
                message = (
                    f"✅ *Status: CLOCKED OUT*\n\n"
                    f"📅 Date: {check_date.strftime('%B %d, %Y')}\n"
                    f"🆔 Kaba ID: `{user['kaba_id']}`\n\n"
                    f"Both clock-in and clock-out records found. Shift complete!"
                )
            elif status == AttendanceStatus.CLOCKED_IN:
                message = (
                    f"⏰ *Status: CLOCKED IN*\n\n"
                    f"📅 Date: {check_date.strftime('%B %d, %Y')}\n"
                    f"🆔 Kaba ID: `{user['kaba_id']}`\n\n"
                    f"Clock-in record found, but no clock-out yet."
                )
            else:  # NO_RECORD
                message = (
                    f"❌ *Status: NO RECORD*\n\n"
                    f"📅 Date: {check_date.strftime('%B %d, %Y')}\n"
                    f"🆔 Kaba ID: `{user['kaba_id']}`\n\n"
                    f"No attendance records found for this date."
                )
            
            await update.message.reply_text(message, parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"Error in check command: {e}")
            await update.message.reply_text(
                f"❌ *Error checking attendance*\n\n"
                f"Failed to scrape portal. Check logs for details.\n\n"
                f"Error: `{str(e)}`",
                parse_mode="Markdown"
            )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /test command - simulate scheduler check with custom time."""
    user = await db.get_user(update.effective_user.id)
    
    if not user:
        await update.message.reply_text(
            "❌ You haven't registered yet!\n\n"
            "Use /register to set up your schedule first."
        )
        return
    
    # Parse time from command arguments
    if not context.args:
        await update.message.reply_text(
            "🧪 *Test Scheduler Logic*\n\n"
            "Usage: `/test HH:MM`\n\n"
            "Example: `/test 17:30` - Simulates scheduler running at 17:30\n\n"
            "This will:\n"
            "• Check if it's a working day\n"
            "• Run the scheduler logic as if current time is HH:MM\n"
            "• Show what alerts would be sent (without actually sending them)\n\n"
            "Perfect for testing without waiting for actual shift times!",
            parse_mode="Markdown"
        )
        return
    
    # Parse the time
    try:
        test_time_str = context.args[0]
        test_time = parse_time(test_time_str)
        if not test_time:
            raise ValueError("Invalid time format")
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Invalid time format!\n\n"
            "Please use HH:MM format (e.g., 17:30)"
        )
        return
    
    today = get_current_time_eat().date()
    
    # Parse user's shift times
    start_time = parse_time(user['start_time'])
    end_time = parse_time(user['end_time'])
    working_day_indices = [int(d) for d in user['working_days'].split(',')]
    
    # Build status message
    status_msg = (
        f"🧪 *Test Mode - Simulating {test_time_str}*\n\n"
        f"📅 Date: {today.strftime('%B %d, %Y')}\n"
        f"🆔 Kaba ID: `{user['kaba_id']}`\n"
        f"🕐 Your shift: {user['start_time']} - {user['end_time']}\n\n"
    )
    
    # Check if holiday
    if is_ethiopian_holiday(today):
        status_msg += "🎉 *Today is an Ethiopian holiday!*\n\nScheduler would skip this day.\n"
        await update.message.reply_text(status_msg, parse_mode="Markdown")
        return
    
    # Check if working day
    if today.weekday() not in working_day_indices:
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        status_msg += f"📅 *Today is {day_names[today.weekday()]} - Not a working day!*\n\nScheduler would skip this day.\n"
        await update.message.reply_text(status_msg, parse_mode="Markdown")
        return
    
    status_msg += "✅ Working day - scheduler would run\n\n"
    
    # Determine which window
    morning_window = test_time >= start_time
    evening_window = test_time >= end_time
    
    if evening_window:
        status_msg += "🌆 **Evening Window (Clock-out Check)**\n\n"
        
        # Check portal
        await update.message.reply_text(status_msg + "🔄 Checking portal...", parse_mode="Markdown")
        
        portal_url = os.getenv("PORTAL_URL", "https://example.com/attendance")
        async with aiohttp.ClientSession() as session:
            portal_status = await check_attendance_async(session, user['kaba_id'], today, portal_url)
        
        if portal_status == AttendanceStatus.CLOCKED_OUT:
            status_msg += (
                "Portal Status: ✅ CLOCKED_OUT\n\n"
                "📬 Would send message:\n"
                "'👋 Clock-Out Confirmed! Your shift is complete. See you tomorrow!'\n\n"
                f"DB update: last_evening_success_date = {today.isoformat()}"
            )
        elif portal_status == AttendanceStatus.CLOCKED_IN:
            from datetime import datetime, timedelta
            end_datetime = datetime.combine(today, end_time)
            current_datetime = datetime.combine(today, test_time)
            grace_period = timedelta(minutes=25)
            
            if current_datetime >= end_datetime + grace_period:
                status_msg += (
                    "Portal Status: ⏰ CLOCKED_IN (still clocked in)\n"
                    f"Time since shift end: {(current_datetime - end_datetime).seconds // 60} minutes\n\n"
                    "🚨 Would send CRITICAL ALERT:\n"
                    "'🚨 CRITICAL ALERT! ⚠️ You have missed the clock-out window! ⏰ Please clock out NOW!'\n\n"
                    f"DB update: last_evening_alert_date = {today.isoformat()}"
                )
            else:
                mins_until_alert = 25 - ((current_datetime - end_datetime).seconds // 60)
                status_msg += (
                    "Portal Status: ⏰ CLOCKED_IN (still clocked in)\n"
                    f"Time since shift end: {(current_datetime - end_datetime).seconds // 60} minutes\n"
                    f"Grace period remaining: {mins_until_alert} minutes\n\n"
                    "⏳ No alert yet - within grace period\n"
                    f"Alert would be sent at {(end_datetime + grace_period).strftime('%H:%M')}"
                )
        else:
            status_msg += (
                "Portal Status: ❌ NO_RECORD\n\n"
                "⚠️ No action - No clock-in found, skipping clock-out check"
            )
    
    elif morning_window:
        status_msg += "🌅 **Morning Window (Clock-in Check)**\n\n"
        
        # Check portal
        await update.message.reply_text(status_msg + "🔄 Checking portal...", parse_mode="Markdown")
        
        portal_url = os.getenv("PORTAL_URL", "https://example.com/attendance")
        async with aiohttp.ClientSession() as session:
            portal_status = await check_attendance_async(session, user['kaba_id'], today, portal_url)
        
        if portal_status == AttendanceStatus.CLOCKED_IN:
            status_msg += (
                "Portal Status: ✅ CLOCKED_IN\n\n"
                "📬 Would send message:\n"
                "'✅ Clock-In Confirmed! You've successfully clocked in. Have a great day!'\n\n"
                f"DB update: last_morning_success_date = {today.isoformat()}"
            )
        elif portal_status == AttendanceStatus.NO_RECORD:
            from datetime import datetime, timedelta
            start_datetime = datetime.combine(today, start_time)
            current_datetime = datetime.combine(today, test_time)
            grace_period = timedelta(minutes=10)
            
            if current_datetime >= start_datetime + grace_period:
                status_msg += (
                    "Portal Status: ❌ NO_RECORD\n"
                    f"Time since shift start: {(current_datetime - start_datetime).seconds // 60} minutes\n\n"
                    "🚨 Would send CRITICAL ALERT:\n"
                    "'🚨 CRITICAL ALERT! ⚠️ You have missed the clock-in window! ⏰ Please clock in NOW!'\n\n"
                    f"DB update: last_morning_alert_date = {today.isoformat()}"
                )
            else:
                mins_until_alert = 10 - ((current_datetime - start_datetime).seconds // 60)
                status_msg += (
                    "Portal Status: ❌ NO_RECORD\n"
                    f"Time since shift start: {(current_datetime - start_datetime).seconds // 60} minutes\n"
                    f"Grace period remaining: {mins_until_alert} minutes\n\n"
                    "⏳ No alert yet - within grace period\n"
                    f"Alert would be sent at {(start_datetime + grace_period).strftime('%H:%M')}"
                )
        else:
            status_msg += (
                "Portal Status: ✅ Already clocked out\n\n"
                "✅ No action needed"
            )
    
    else:
        status_msg += (
            f"⏰ **Before Shift Start ({user['start_time']})**\n\n"
            "⏳ Scheduler would skip - too early in the day"
        )
    
    await update.message.reply_text(status_msg)  # Plain text, no Markdown parsing


# ==================== REGISTRATION CONVERSATION ====================

async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the registration conversation."""
    await update.message.reply_text(
        "📝 *Let's set up your attendance tracking!*\n\n"
        "Please enter your *Kaba ID* (e.g., 12345):",
        parse_mode="Markdown"
    )
    return ASK_KABA_ID


async def ask_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store Kaba ID and ask for start time."""
    kaba_id = update.message.text.strip()
    context.user_data['kaba_id'] = kaba_id
    
    await update.message.reply_text(
        f"✅ Kaba ID: `{kaba_id}`\n\n"
        f"Now, what time does your shift *start*?\n"
        f"Please use HH:MM format (e.g., 08:00):",
        parse_mode="Markdown"
    )
    return ASK_START_TIME


async def ask_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and store start time, ask for end time."""
    start_time_str = update.message.text.strip()
    
    if not parse_time(start_time_str):
        await update.message.reply_text(
            "❌ Invalid time format!\n\n"
            "Please use HH:MM format (e.g., 08:00):"
        )
        return ASK_START_TIME
    
    context.user_data['start_time'] = start_time_str
    
    await update.message.reply_text(
        f"✅ Shift Start: {start_time_str}\n\n"
        f"What time does your shift *end*?\n"
        f"Please use HH:MM format (e.g., 17:00):",
        parse_mode="Markdown"
    )
    return ASK_END_TIME


async def ask_working_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and store end time, ask for working days."""
    end_time_str = update.message.text.strip()
    
    if not parse_time(end_time_str):
        await update.message.reply_text(
            "❌ Invalid time format!\n\n"
            "Please use HH:MM format (e.g., 17:00):"
        )
        return ASK_END_TIME
    
    context.user_data['end_time'] = end_time_str
    
    # Create keyboard with only weekdays option
    keyboard = [
        ["Mon-Fri (Weekdays)"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    await update.message.reply_text(
        "📅 *Which days do you work?*\n\n"
        "You can:\n"
        "• Select multiple days separated by commas or spaces\n"
        "• Use shortcuts like 'Mon', 'Tue', etc.\n"
        "• Or choose 'Mon-Fri (Weekdays)' for weekdays\n\n"
        "Examples:\n"
        "• `Monday, Wednesday, Friday`\n"
        "• `Mon Tue Thu`\n"
        "• `Mon-Fri (Weekdays)`\n\n"
        "Use the keyboard or type your days:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return ASK_WORKING_DAYS


async def confirm_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse working days and ask for confirmation."""
    days_input = update.message.text.strip()
    
    # Map day names to indices
    day_map = {
        "monday": 0, "mon": 0,
        "tuesday": 1, "tue": 1,
        "wednesday": 2, "wed": 2,
        "thursday": 3, "thu": 3,
        "friday": 4, "fri": 4,
        "saturday": 5, "sat": 5,
        "sunday": 6, "sun": 6,
    }
    
    day_indices = []
    
    # Handle "Mon-Fri (Weekdays)" preset
    if "mon-fri" in days_input.lower() or "weekdays" in days_input.lower():
        day_indices = [0, 1, 2, 3, 4]
    else:
        # Parse individual days
        day_parts = [d.strip().lower() for d in days_input.replace(',', ' ').split()]
        
        for day_part in day_parts:
            if day_part in day_map:
                idx = day_map[day_part]
                if idx not in day_indices:
                    day_indices.append(idx)
        
        if not day_indices:
            await update.message.reply_text(
                "❌ No valid days found!\n\n"
                "Please enter days like: Monday,Tuesday,Friday"
            )
            return ASK_WORKING_DAYS
    
    day_indices.sort()
    context.user_data['working_days'] = ','.join(map(str, day_indices))
    
    # Show summary
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    selected_days = [day_names[i] for i in day_indices]
    
    keyboard = [["✅ Confirm", "❌ Cancel"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    await update.message.reply_text(
        f"📋 *Please confirm your details:*\n\n"
        f"🆔 Kaba ID: `{context.user_data['kaba_id']}`\n"
        f"🕐 Shift Start: {context.user_data['start_time']}\n"
        f"🕔 Shift End: {context.user_data['end_time']}\n"
        f"📅 Working Days: {', '.join(selected_days)}\n\n"
        f"Is this correct?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    return CONFIRM


async def save_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the registration to database."""
    response = update.message.text.strip().lower()
    
    if "cancel" in response or "❌" in response:
        await update.message.reply_text(
            "❌ Registration cancelled.\n\n"
            "Use /register to start over.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    if "confirm" not in response and "✅" not in response:
        await update.message.reply_text(
            "Please tap '✅ Confirm' or '❌ Cancel'."
        )
        return CONFIRM
    
    # Save to database
    await db.register_user(
        user_id=update.effective_user.id,
        kaba_id=context.user_data['kaba_id'],
        start_time=context.user_data['start_time'],
        end_time=context.user_data['end_time'],
        working_days=context.user_data['working_days']
    )
    
    await update.message.reply_text(
        "✅ *Registration successful!*\n\n"
        "I'll now monitor your attendance and send you alerts if needed.\n\n"
        "Use /status to view your settings anytime.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel registration conversation."""
    await update.message.reply_text(
        "❌ Registration cancelled.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# ==================== SCHEDULER - DB-FIRST OPTIMIZED ====================

async def check_all_users_attendance() -> None:
    """
    Master scheduler job that checks attendance for all users.
    Implements DB-first optimization to minimize portal scraping.
    """
    logger.info("Starting attendance check cycle...")
    
    now = get_current_time_eat()
    today = now.date()
    current_time = now.time()
    
    users = await db.get_all_active_users()
    logger.info(f"Checking {len(users)} registered users")
    
    # Create shared aiohttp session for all scraping
    async with aiohttp.ClientSession() as session:
        for user in users:
            try:
                await check_user_attendance(user, today, current_time, session)
            except Exception as e:
                logger.error(f"Error checking user {user['user_id']}: {e}")


async def check_user_attendance(
    user: dict,
    today: date,
    current_time: time,
    session: aiohttp.ClientSession
) -> None:
    """
    Check attendance for a single user with DB-first optimization.
    
    Steps:
    1. Context Filters (fastest)
    2. DB State Check (fast)
    3. Conditional Scrape (slow - only if needed)
    4. Decision & Action
    """
    user_id = user['user_id']
    
    # ===== STEP 1: CONTEXT FILTERS =====
    
    # Check if today is Ethiopian holiday
    if is_ethiopian_holiday(today):
        logger.debug(f"User {user_id}: Skipping (Ethiopian holiday)")
        return
    
    # Check if today is a working day for this user
    working_day_indices = [int(d) for d in user['working_days'].split(',')]
    if today.weekday() not in working_day_indices:
        logger.debug(f"User {user_id}: Skipping (not a working day)")
        return
    
    # Parse shift times
    start_time = parse_time(user['start_time'])
    end_time = parse_time(user['end_time'])
    
    # ===== STEP 2: DB STATE CHECK =====
    
    # Determine which window we're in
    morning_window = current_time >= start_time
    evening_window = current_time >= end_time
    
    should_scrape = False
    check_type = None  # 'morning' or 'evening'
    
    if evening_window:
        # Evening check has priority
        check_type = 'evening'
        
        # Check if we already handled evening today
        if user['last_evening_success_date'] == today.isoformat():
            logger.debug(f"User {user_id}: Already confirmed clock-out today")
            return
        
        # NOTE: We removed the alert skip check here to allow confirmation after alert
        # Even if alert was sent, we continue checking to send confirmation when user clocks out
        
        should_scrape = True
        
    elif morning_window:
        # Morning check
        check_type = 'morning'
        
        # Check if we already handled morning today
        if user['last_morning_success_date'] == today.isoformat():
            logger.debug(f"User {user_id}: Already confirmed clock-in today")
            return
        
        if user['last_morning_alert_date'] == today.isoformat():
            logger.debug(f"User {user_id}: Already alerted for clock-in today")
            return
        
        should_scrape = True
    
    else:
        # Too early in the day
        logger.debug(f"User {user_id}: Before shift start time")
        return
    
    # ===== STEP 3: CONDITIONAL SCRAPE =====
    
    if not should_scrape:
        return
    
    logger.info(f"User {user_id}: Scraping attendance ({check_type} check)")
    
    portal_url = os.getenv("PORTAL_URL", "https://example.com/attendance")
    status = await check_attendance_async(session, user['kaba_id'], today, portal_url)
    
    # ===== STEP 4: DECISION & ACTION =====
    
    if check_type == 'morning':
        await handle_morning_check(user_id, status, start_time, current_time, today)
    elif check_type == 'evening':
        await handle_evening_check(user_id, status, end_time, current_time, today)


async def handle_morning_check(
    user_id: int,
    status: AttendanceStatus,
    start_time: time,
    current_time: time,
    today: date
) -> None:
    """Handle morning (clock-in) check logic."""
    from telegram import Bot
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    
    if status == AttendanceStatus.CLOCKED_IN:
        # Update DB FIRST to prevent duplicates if message send is slow
        logger.info(f"User {user_id}: Clock-in detected, updating DB to prevent duplicates")
        await db.update_morning_success(user_id, today)
        logger.info(f"User {user_id}: DB updated - last_morning_success_date = {today.isoformat()}")
        
        # Then send confirmation message
        try:
            formatted_time = current_time.strftime("%I:%M %p")
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ *Clock-In Confirmed!*\n\n"
                     f"🕒 *Time:* {formatted_time}\n"
                     f"📍 *Status:* Checked In\n\n"
                     f"Have a great day!",
                parse_mode="Markdown"
            )
            logger.info(f"User {user_id}: Morning success notification sent successfully")
        except Exception as e:
            logger.error(f"User {user_id}: Failed to send message (DB already updated): {e}")
    
    elif status == AttendanceStatus.NO_RECORD:
        # Check if 10 minutes past start time
        start_datetime = datetime.combine(today, start_time)
        current_datetime = datetime.combine(today, current_time)
        grace_period = timedelta(minutes=10)
        
        if current_datetime >= start_datetime + grace_period:
            # Send critical alert
            await bot.send_message(
                chat_id=user_id,
                text="🚨 *CRITICAL ALERT!*\n\n"
                     "⚠️ You have missed the clock-in window!\n"
                     "⏰ Please clock in NOW on the company portal!",
                parse_mode="Markdown"
            )
            await db.update_morning_alert(user_id, today)
            logger.info(f"User {user_id}: Morning alert sent (10+ mins late)")


async def handle_evening_check(
    user_id: int,
    status: AttendanceStatus,
    end_time: time,
    current_time: time,
    today: date
) -> None:
    """Handle evening (clock-out) check logic."""
    from telegram import Bot
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    
    if status == AttendanceStatus.CLOCKED_OUT:
        # Update DB FIRST to prevent duplicates if message send is slow
        logger.info(f"User {user_id}: Clock-out detected, updating DB to prevent duplicates")
        await db.update_evening_success(user_id, today)
        logger.info(f"User {user_id}: DB updated - last_evening_success_date = {today.isoformat()}")
        
        # Then send confirmation message
        try:
            formatted_time = current_time.strftime("%I:%M %p")
            await bot.send_message(
                chat_id=user_id,
                text=f"👋 *Clock-Out Confirmed!*\n\n"
                     f"🕒 *Time:* {formatted_time}\n"
                     f"📍 *Status:* Checked Out\n\n"
                     f"Your shift is complete. See you tomorrow!",
                parse_mode="Markdown"
            )
            logger.info(f"User {user_id}: Evening success notification sent successfully")
        except Exception as e:
            logger.error(f"User {user_id}: Failed to send message (DB already updated): {e}")
    
    elif status == AttendanceStatus.CLOCKED_IN:
        # Still clocked in - check if 25 minutes past end time
        end_datetime = datetime.combine(today, end_time)
        current_datetime = datetime.combine(today, current_time)
        grace_period = timedelta(minutes=25)
        
        if current_datetime >= end_datetime + grace_period:
            # Check retry limits before sending alert
            user = await db.get_user(user_id)
            if not user:
                return
            
            # Determine if this is a new day or same day
            is_new_day = user.get('last_evening_alert_date') != today.isoformat()
            
            if not is_new_day:
                # Same day - check alert count and timing
                alert_count = user.get('last_evening_alert_count', 0)
                
                # Max 3 alerts per day
                if alert_count >= 3:
                    logger.info(f"User {user_id}: Max 3 evening alerts already sent today, skipping")
                    return
                
                # Check 15-min gap
                last_alert_time_str = user.get('last_evening_alert_time')
                if last_alert_time_str:
                    try:
                        last_alert_time = datetime.fromisoformat(last_alert_time_str)
                        time_since_last = (current_datetime - last_alert_time).total_seconds() / 60
                        
                        if time_since_last < 15:
                            logger.debug(f"User {user_id}: Only {time_since_last:.1f}min since last alert, waiting")
                            return
                    except:
                        pass  # Invalid datetime, continue
            
            # Send critical alert
            alert_number = 1 if is_new_day else (user.get('last_evening_alert_count', 0) + 1)
            logger.info(f"User {user_id}: Sending evening alert #{alert_number}")
            
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="🚨 *CRITICAL ALERT!*\n\n"
                         "⚠️ You have missed the clock-out window!\n"
                         "⏰ Please clock out NOW on the company portal!",
                    parse_mode="Markdown"
                )
                logger.info(f"User {user_id}: Evening alert #{alert_number} sent (25+ mins late)")
            except Exception as e:
                logger.error(f"User {user_id}: Failed to send alert: {e}")
                return  # Don't update DB if message failed
            
            # Update DB with retry tracking
            await db.update_evening_alert_with_count(
                user_id, 
                today, 
                current_datetime.isoformat(), 
                is_new_day
            )


# ==================== MAIN APPLICATION ====================

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
    
    # Create application (SSL bypass is handled globally at import time)
    application = Application.builder().token(token).post_init(post_init).build()
    
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
