import os
import logging
from datetime import datetime, time, timedelta, date
import aiohttp
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

from .database import Database
from .scraper import check_attendance_async, AttendanceStatus
from .utils import EAT, get_current_time_eat, is_ethiopian_holiday, parse_time, escape_md

logger = logging.getLogger(__name__)
db = Database(os.getenv("DATABASE_PATH", "kabaguard.db"))

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
) -> str:
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
        return "skipped_holiday"
    
    # Check if today is a working day for this user
    working_day_indices = [int(d) for d in user['working_days'].split(',')]
    if today.weekday() not in working_day_indices:
        logger.debug(f"User {user_id}: Skipping (not a working day)")
        return "skipped_non_working_day"
    
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
            return "already_confirmed_evening"
        
        # NOTE: We removed the alert skip check here to allow confirmation after alert
        should_scrape = True
        
    elif morning_window:
        # Morning check
        check_type = 'morning'
        
        # Check if we already handled morning today
        if user['last_morning_success_date'] == today.isoformat():
            logger.debug(f"User {user_id}: Already confirmed clock-in today")
            return "already_confirmed_morning"
        
        # NOTE: We removed the alert skip check here to allow confirmation after an alert
        should_scrape = True
    
    else:
        # Too early in the day
        logger.debug(f"User {user_id}: Before shift start time")
        return "before_shift"
    
    # ===== STEP 3: CONDITIONAL SCRAPE =====
    
    if not should_scrape:
        return "unknown_state"
    
    logger.info(f"User {user_id}: Scraping attendance ({check_type} check)")
    
    portal_url = os.getenv("PORTAL_URL", "https://example.com/attendance")
    status, details = await check_attendance_async(session, user['kaba_id'], today, portal_url)
    
    if status == AttendanceStatus.ERROR:
        logger.warning(f"User {user_id}: Skipping alert due to scraper error.")
        return "error"

    # ===== STEP 4: DECISION & ACTION =====
    
    if check_type == 'morning':
        return await handle_morning_check(user_id, status, start_time, current_time, today, details)
    elif check_type == 'evening':
        return await handle_evening_check(user_id, status, end_time, current_time, today, details)
    return "unknown_check_type"


async def handle_morning_check(
    user_id: int,
    status: AttendanceStatus,
    start_time: time,
    current_time: time,
    today: date,
    details: dict = None
) -> str:
    """Handle morning (clock-in) check logic."""
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    
    if status in (AttendanceStatus.CLOCKED_IN, AttendanceStatus.CLOCKED_OUT):
        # Update DB FIRST to prevent duplicates if message send is slow
        logger.info(f"User {user_id}: Clock-in detected, updating DB to prevent duplicates")
        await db.update_morning_success(user_id, today)
        logger.info(f"User {user_id}: DB updated - last_morning_success_date = {today.isoformat()}")
        
        # Then send confirmation message
        try:
            if details and details.get('clock_in'):
                formatted_time = escape_md(details['clock_in']['time'])
                location = escape_md(details['clock_in']['location'])
            else:
                formatted_time = escape_md(current_time.strftime("%I:%M %p"))
                location = "Unknown"
                
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ *Clock-In Confirmed!*\n\n"
                     f"🕒 *Time:* {formatted_time}\n"
                     f"📍 *Location:* {location}\n\n"
                     f"Have a great day!",
                parse_mode="Markdown"
            )
            logger.info(f"User {user_id}: Morning success notification sent successfully")
            return "morning_success_sent"
        except Exception as e:
            logger.error(f"User {user_id}: Failed to send message (DB already updated): {e}")
            return "error"
    
    elif status == AttendanceStatus.NO_RECORD:
        # Check if past start time + 5 mins grace period
        start_datetime = datetime.combine(today, start_time, tzinfo=EAT)
        current_datetime = datetime.combine(today, current_time, tzinfo=EAT)
        grace_period = timedelta(minutes=5)
        
        if current_datetime >= start_datetime + grace_period:
            # Check retry limits before sending alert
            user = await db.get_user(user_id)
            if not user:
                return "user_not_found"
            
            # 1. Check if dismissed today
            if user.get('morning_dismissed_date') == today.isoformat():
                logger.debug(f"User {user_id}: Morning alert was dismissed for today")
                return "dismissed_morning"

            # 2. Check if snoozed
            snooze_str = user.get('morning_snooze_until')
            if snooze_str:
                try:
                    snooze_time = datetime.fromisoformat(snooze_str)
                    if current_datetime < snooze_time:
                        logger.debug(f"User {user_id}: Morning alert is snoozed until {snooze_time}")
                        return "snoozed_morning"
                except ValueError:
                    pass

            # Determine if this is a new day or same day
            is_new_day = user.get('last_morning_alert_date') != today.isoformat()
            
            if not is_new_day:
                # Check 5 min gap
                last_alert_time_str = user.get('last_morning_alert_time')
                print(f'last_alert_time_str: {last_alert_time_str}')
                if last_alert_time_str:
                    try:
                        last_alert_time = datetime.fromisoformat(last_alert_time_str)
                        time_since_last = (current_datetime - last_alert_time).total_seconds() / 60
                        
                        if time_since_last < 5:
                            logger.debug(f"User {user_id}: Only {time_since_last:.1f}min since last alert, waiting 5m")
                            return "waiting_exponential_backoff"
                    except ValueError:
                        pass
            
            # Send critical alert
            alert_number = 1 if is_new_day else (user.get('last_morning_alert_count', 0) + 1)
            logger.info(f"User {user_id}: Sending morning alert #{alert_number}")
            
            keyboard = [
                [
                    InlineKeyboardButton("Snooze clock-in 30'", callback_data="snooze_morning"),
                    InlineKeyboardButton("Dismiss Alert", callback_data="dismiss_morning")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            alert_text = (
                "🚨 *CRITICAL ALERT!*\n\n"
                "⚠️ You have missed the clock-in window!\n\n"
                "_(Note: If the attendance machine already said 'Thank you', it might just be a brief network delay. In that case, you can safely ignore this.)_"
            )
            
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=alert_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                logger.info(f"User {user_id}: Morning alert #{alert_number} sent")
            except Exception as e:
                logger.error(f"User {user_id}: Failed to send alert: {e}")
                return "error" # Don't update DB if message failed
            
            # Update DB with retry tracking
            await db.update_morning_alert_with_count(
                user_id, 
                today, 
                current_datetime.isoformat(), 
                is_new_day
            )
            return "morning_alert_sent"
        else:
            return "waiting_grace_period"
    return "no_action"



async def handle_evening_check(
    user_id: int,
    status: AttendanceStatus,
    end_time: time,
    current_time: time,
    today: date,
    details: dict = None
) -> None:
    """Handle evening (clock-out) check logic."""
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    
    if status == AttendanceStatus.CLOCKED_OUT:
        # Update DB FIRST to prevent duplicates if message send is slow
        logger.info(f"User {user_id}: Clock-out detected, updating DB to prevent duplicates")
        await db.update_evening_success(user_id, today)
        logger.info(f"User {user_id}: DB updated - last_evening_success_date = {today.isoformat()}")
        
        # Then send confirmation message
        try:
            if details and details.get('clock_out'):
                formatted_time = escape_md(details['clock_out']['time'])
                location = escape_md(details['clock_out']['location'])
            else:
                formatted_time = escape_md(current_time.strftime("%I:%M %p"))
                location = "Unknown"
                
            await bot.send_message(
                chat_id=user_id,
                text=f"👋 *Clock-Out Confirmed!*\n\n"
                     f"🕒 *Time:* {formatted_time}\n"
                     f"📍 *Location:* {location}\n\n"
                     f"Your shift is complete. See you tomorrow!",
                parse_mode="Markdown"
            )
            logger.info(f"User {user_id}: Evening success notification sent successfully")
            return "evening_success_sent"
        except Exception as e:
            logger.error(f"User {user_id}: Failed to send message (DB already updated): {e}")
            return "error"
    
    elif status == AttendanceStatus.CLOCKED_IN:
        # Still clocked in - check if 30 minutes past end time
        end_datetime = datetime.combine(today, end_time, tzinfo=EAT)
        current_datetime = datetime.combine(today, current_time, tzinfo=EAT)
        grace_period = timedelta(minutes=30)
        
        if current_datetime >= end_datetime + grace_period:
            # Check retry limits before sending alert
            user = await db.get_user(user_id)
            if not user:
                return "user_not_found"
                
            # 1. Check if dismissed today
            if user.get('evening_dismissed_date') == today.isoformat():
                logger.debug(f"User {user_id}: Evening alert was dismissed for today")
                return "dismissed_evening"

            # 2. Check if snoozed
            snooze_str = user.get('evening_snooze_until')
            if snooze_str:
                try:
                    snooze_time = datetime.fromisoformat(snooze_str)
                    if current_datetime < snooze_time:
                        logger.debug(f"User {user_id}: Evening alert is snoozed until {snooze_time}")
                        return "snoozed_evening"
                except ValueError:
                    pass
            
            # Determine if this is a new day or same day
            is_new_day = user.get('last_evening_alert_date') != today.isoformat()
            
            if not is_new_day:
                # Check 5 min gap
                last_alert_time_str = user.get('last_evening_alert_time')
                if last_alert_time_str:
                    try:
                        last_alert_time = datetime.fromisoformat(last_alert_time_str)
                        time_since_last = (current_datetime - last_alert_time).total_seconds() / 60
                        
                        if time_since_last < 5:
                            logger.debug(f"User {user_id}: Only {time_since_last:.1f}min since last alert, waiting 5m")
                            return "waiting_exponential_backoff"
                    except ValueError:
                        pass
            
            # Send critical alert
            alert_number = 1 if is_new_day else (user.get('last_evening_alert_count', 0) + 1)
            logger.info(f"User {user_id}: Sending evening alert #{alert_number}")
            
            keyboard = [
                [
                    InlineKeyboardButton("Snooze clock-out 30'", callback_data="snooze_evening"),
                    InlineKeyboardButton("Dismiss Alert", callback_data="dismiss_evening")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            alert_text = (
                "🚨 *CRITICAL ALERT!*\n\n"
                "⚠️ You have missed the clock-out window!\n\n"
                "_(Note: If the attendance machine already said 'Thank you', it might just be a brief network delay. In that case, you can safely ignore this.)_"
            )            
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=alert_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                logger.info(f"User {user_id}: Evening alert #{alert_number} sent")
            except Exception as e:
                logger.error(f"User {user_id}: Failed to send alert: {e}")
                return "error" # Don't update DB if message failed
            
            # Update DB with retry tracking
            await db.update_evening_alert_with_count(
                user_id, 
                today, 
                current_datetime.isoformat(), 
                is_new_day
            )
            return "evening_alert_sent"
        else:
            return "waiting_grace_period"
    return "no_action"
