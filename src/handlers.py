import os
import logging
from datetime import datetime, date, timedelta

import aiohttp
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

from .database import Database
from .scraper import check_attendance_async, AttendanceStatus
from .utils import parse_time, get_current_time_eat, is_ethiopian_holiday, escape_md

logger = logging.getLogger(__name__)
db = Database(os.getenv("DATABASE_PATH", "kabaguard.db"))

# Conversation states
ASK_KABA_ID, ASK_START_TIME, ASK_END_TIME, ASK_WORKING_DAYS, CONFIRM = range(5)

# ==================== COMMAND HANDLERS ====================

def _developer_user_ids() -> set[int]:
    """Return Telegram user IDs allowed to use developer-only commands."""
    configured = os.getenv("DEV_USER_IDS", "")
    user_ids = set()
    for item in configured.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            user_ids.add(int(item))
        except ValueError:
            logger.warning("Ignoring invalid DEV_USER_IDS entry: %s", item)
    return user_ids


def _is_developer(update: Update) -> bool:
    """Check if the current Telegram user is allowed to use dev tools."""
    if not update.effective_user:
        return False
    return update.effective_user.id in _developer_user_ids()

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
    test_command_help = ""
    if _is_developer(update):
        test_command_help = "/test HH:MM [YYYY-MM-DD] - Developer dry-run scheduler simulation\n"

    await update.message.reply_text(
        "*KabaGuard Commands:*\n\n"
        "/start - Welcome message\n"
        "/register - Register or update your schedule\n"
        "/status - View your current settings\n"
        "/check YYYY-MM-DD - Test scraper for a specific date\n"
        f"{test_command_help}"
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

    try:
        date_str = context.args[0]
        check_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid date format!\n\n"
            "Please use YYYY-MM-DD format (e.g., 2026-02-04)"
        )
        return
    
    await update.message.reply_text(
        f"🔄 Checking attendance for {check_date.strftime('%B %d, %Y')}...\n\n"
        f"Scraping portal with Kaba ID: `{user['kaba_id']}`",
        parse_mode="Markdown"
    )
    
    portal_url = os.getenv("PORTAL_URL", "https://example.com/attendance")
    
    async with aiohttp.ClientSession() as session:
        try:
            status, details = await check_attendance_async(session, user['kaba_id'], check_date, portal_url)
            
            in_info = "Not found"
            out_info = "Not found"
            
            if details:
                if details.get('clock_in'):
                    in_time = escape_md(details['clock_in']['time'])
                    in_loc = escape_md(details['clock_in']['location'])
                    in_info = f"{in_time} ({in_loc})"
                
                if details.get('clock_out'):
                    out_time = escape_md(details['clock_out']['time'])
                    out_loc = escape_md(details['clock_out']['location'])
                    out_info = f"{out_time} ({out_loc})"
            
            if status == AttendanceStatus.CLOCKED_OUT:
                message = (
                    f"✅ *Status: CLOCKED OUT*\n\n"
                    f"📅 Date: {check_date.strftime('%B %d, %Y')}\n"
                    f"🆔 Kaba ID: `{user['kaba_id']}`\n"
                    f"📥 Clock-In: {in_info}\n"
                    f"📤 Clock-Out: {out_info}\n\n"
                    f"Both clock-in and clock-out records found. Shift complete!"
                )
            elif status == AttendanceStatus.CLOCKED_IN:
                message = (
                    f"⏰ *Status: CLOCKED IN*\n\n"
                    f"📅 Date: {check_date.strftime('%B %d, %Y')}\n"
                    f"🆔 Kaba ID: `{user['kaba_id']}`\n"
                    f"📥 Clock-In: {in_info}\n"
                    f"📤 Clock-Out: {out_info}\n\n"
                    f"Clock-in record found, but no clock-out yet."
                )
            elif status == AttendanceStatus.ERROR:
                message = (
                    f"⚠️ *Status: ERROR*\n\n"
                    f"📅 Date: {check_date.strftime('%B %d, %Y')}\n"
                    f"🆔 Kaba ID: `{user['kaba_id']}`\n\n"
                    f"A network or portal error occurred while checking attendance. The portal might be down."
                )
            else:
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
    """Handle /test command - simulate scheduler check with custom time/date."""
    if not _is_developer(update):
        await update.message.reply_text(
            "❌ This is a developer-only command."
        )
        return

    user = await db.get_user(update.effective_user.id)
    
    if not user:
        await update.message.reply_text(
            "❌ You haven't registered yet!\n\n"
            "Use /register to set up your schedule first."
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "🧪 *Test Scheduler Logic*\n\n"
            "Usage: `/test HH:MM [YYYY-MM-DD]` or `/test now`\n\n"
            "Examples:\n"
            "`/test 17:30` - Simulates today at 17:30\n"
            "`/test 17:30 2026-05-15` - Simulates May 15, 2026 at 17:30\n\n"
            "`/test now` - Dry-runs the actual current date/time\n\n"
            "This will:\n"
            "• Check if the selected date is a working day or holiday\n"
            "• Run the scheduler logic as if current time/date is the value you entered\n"
            "• Show what alerts would be sent (without actually sending them)\n\n"
            "Perfect for testing without waiting for actual shift times!",
            parse_mode="Markdown"
        )
        return

    if len(context.args) == 1 and context.args[0].lower() in ("now", "live", "current"):
        now = get_current_time_eat()
        from .testing import run_test_simulation
        await run_test_simulation(update, context, user, now.time(), now.date())
        return

    if len(context.args) > 2:
        await update.message.reply_text(
            "❌ Too many arguments!\n\n"
            "Usage: `/test HH:MM [YYYY-MM-DD]` or `/test now`",
            parse_mode="Markdown"
        )
        return
    
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
    
    test_date = get_current_time_eat().date()
    if len(context.args) > 1:
        try:
            test_date = datetime.strptime(context.args[1], "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid date format!\n\n"
                "Please use YYYY-MM-DD format (e.g., 2026-05-15)"
            )
            return
    
    from .testing import run_test_simulation
    await run_test_simulation(update, context, user, test_time, test_date)


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
    
    if "mon-fri" in days_input.lower() or "weekdays" in days_input.lower():
        day_indices = [0, 1, 2, 3, 4]
    else:
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


# ==================== CALLBACK QUERY LOGIC ====================

async def alert_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from alert inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    action = query.data
    message_text = ""
    if query.message:
        message_text = query.message.text or query.message.caption or ""

    # Older alert messages may have been sent with the wrong callback data.
    # Trust the visible alert text when it clearly identifies clock-in/out.
    lowered_message = message_text.lower()
    if "clock-out" in lowered_message and action.endswith("_morning"):
        action = action.replace("_morning", "_evening")
    elif "clock-in" in lowered_message and action.endswith("_evening"):
        action = action.replace("_evening", "_morning")
    
    now = get_current_time_eat()
    today = now.date()
    
    if action == "snooze_morning":
        snooze_until = now + timedelta(minutes=30)
        await db.set_morning_snooze(user_id, snooze_until.isoformat())
        await query.edit_message_text(
            f"💤 *Morning clock-in alert snoozed!*\n\nI won't remind you again until {snooze_until.strftime('%I:%M %p')}.",
            parse_mode="Markdown"
        )
    elif action == "dismiss_morning":
        await db.set_morning_dismissed(user_id, today.isoformat())
        await query.edit_message_text(
            "✅ *Morning alert dismissed!*\n\nI won't remind you again for this morning's clock-in.",
            parse_mode="Markdown"
        )
    elif action == "snooze_evening":
        snooze_until = now + timedelta(minutes=30)
        await db.set_evening_snooze(user_id, snooze_until.isoformat())
        await query.edit_message_text(
            f"💤 *Evening clock-out alert snoozed!*\n\nI won't remind you again until {snooze_until.strftime('%I:%M %p')}.",
            parse_mode="Markdown"
        )
    elif action == "dismiss_evening":
        await db.set_evening_dismissed(user_id, today.isoformat())
        await query.edit_message_text(
            "✅ *Evening alert dismissed!*\n\nI won't remind you again for this evening's clock-out.",
            parse_mode="Markdown"
        )
