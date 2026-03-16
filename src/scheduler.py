import os
import logging
from datetime import datetime, time, timedelta, date
import aiohttp
from telegram import Bot

from .database import Database
from .scraper import check_attendance_async, AttendanceStatus
from .utils import get_current_time_eat, is_ethiopian_holiday, parse_time, escape_md

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
        should_scrape = True
        
    elif morning_window:
        # Morning check
        check_type = 'morning'
        
        # Check if we already handled morning today
        if user['last_morning_success_date'] == today.isoformat():
            logger.debug(f"User {user_id}: Already confirmed clock-in today")
            return
        
        # NOTE: We removed the alert skip check here to allow confirmation after an alert
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
    status, details = await check_attendance_async(session, user['kaba_id'], today, portal_url)
    
    if status == AttendanceStatus.ERROR:
        logger.warning(f"User {user_id}: Skipping alert due to scraper error.")
        return

    # ===== STEP 4: DECISION & ACTION =====
    
    if check_type == 'morning':
        await handle_morning_check(user_id, status, start_time, current_time, today, details)
    elif check_type == 'evening':
        await handle_evening_check(user_id, status, end_time, current_time, today, details)


async def handle_morning_check(
    user_id: int,
    status: AttendanceStatus,
    start_time: time,
    current_time: time,
    today: date,
    details: dict = None
) -> None:
    """Handle morning (clock-in) check logic."""
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    
    if status == AttendanceStatus.CLOCKED_IN:
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
        except Exception as e:
            logger.error(f"User {user_id}: Failed to send message (DB already updated): {e}")
    
    elif status == AttendanceStatus.NO_RECORD:
        # Check if past start time (no grace period)
        start_datetime = datetime.combine(today, start_time)
        current_datetime = datetime.combine(today, current_time)
        
        if current_datetime >= start_datetime:
            # Check retry limits before sending alert
            user = await db.get_user(user_id)
            if not user:
                return
            
            # Determine if this is a new day or same day
            is_new_day = user.get('last_morning_alert_date') != today.isoformat()
            
            if not is_new_day:
                # Same day - check alert count and timing
                alert_count = user.get('last_morning_alert_count', 0)
                
                # Calculate required gap using exponential backoff (5 min, 10 min, 20 min, 40 min...)
                if alert_count > 0:
                    required_gap_minutes = 5 * (2 ** (alert_count - 1))
                else:
                    required_gap_minutes = 5  # Fallback
                
                # Check gap
                last_alert_time_str = user.get('last_morning_alert_time')
                if last_alert_time_str:
                    try:
                        last_alert_time = datetime.fromisoformat(last_alert_time_str)
                        time_since_last = (current_datetime - last_alert_time).total_seconds() / 60
                        
                        if time_since_last < required_gap_minutes:
                            logger.debug(f"User {user_id}: Only {time_since_last:.1f}min since last alert (need {required_gap_minutes}min), waiting")
                            return
                    except:
                        pass  # Invalid datetime, continue
            
            # Send critical alert
            alert_number = 1 if is_new_day else (user.get('last_morning_alert_count', 0) + 1)
            logger.info(f"User {user_id}: Sending morning alert #{alert_number}")
            
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="🚨 *CRITICAL ALERT!*\n\n"
                         "⚠️ You have missed the clock-in window!",
                    parse_mode="Markdown"
                )
                logger.info(f"User {user_id}: Morning alert #{alert_number} sent (0+ mins late)")
            except Exception as e:
                logger.error(f"User {user_id}: Failed to send alert: {e}")
                return  # Don't update DB if message failed
            
            # Update DB with retry tracking
            await db.update_morning_alert_with_count(
                user_id, 
                today, 
                current_datetime.isoformat(), 
                is_new_day
            )


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
                
                # Calculate required gap using exponential backoff (5 min, 10 min, 20 min, 40 min...)
                if alert_count > 0:
                    required_gap_minutes = 5 * (2 ** (alert_count - 1))
                else:
                    required_gap_minutes = 5  # Fallback
                
                # Check gap
                last_alert_time_str = user.get('last_evening_alert_time')
                if last_alert_time_str:
                    try:
                        last_alert_time = datetime.fromisoformat(last_alert_time_str)
                        time_since_last = (current_datetime - last_alert_time).total_seconds() / 60
                        
                        if time_since_last < required_gap_minutes:
                            logger.debug(f"User {user_id}: Only {time_since_last:.1f}min since last alert (need {required_gap_minutes}min), waiting")
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
                         "⚠️ You have missed the clock-out window!",
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
