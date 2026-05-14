"""
Developer-only dry-run simulation for KabaGuard.

This module intentionally does not call the production scheduler handlers,
because those handlers update the database and send Telegram messages.
"""

import os
from datetime import date, datetime, time, timedelta

import aiohttp
from telegram import Update
from telegram.ext import ContextTypes

from .scraper import AttendanceStatus, check_attendance_async
from .utils import EAT, is_ethiopian_holiday, parse_time


def _format_outcome(outcome: str, details: dict | None = None) -> str:
    """Return a Telegram-friendly summary for a dry-run outcome."""
    if outcome == "skipped_holiday":
        return "🎉 *Holiday*\n\nThe scheduler would skip this check."
    if outcome == "skipped_non_working_day":
        return "📅 *Not a working day*\n\nThe scheduler would skip this check."
    if outcome == "already_confirmed_morning":
        return "🌅 *Morning Window*\n\nClock-in is already confirmed for this date."
    if outcome == "already_confirmed_evening":
        return "🌆 *Evening Window*\n\nClock-out is already confirmed for this date."
    if outcome == "before_shift":
        return "⏰ *Before Shift Start*\n\nThe scheduler would skip this check."
    if outcome == "error":
        return "⚠️ *Portal Error*\n\nThe scheduler would skip alerts because scraping was inconclusive."
    if outcome == "snoozed_morning":
        return "💤 *Morning Alert Snoozed*\n\nThe scheduler would skip the clock-in alert."
    if outcome == "snoozed_evening":
        return "💤 *Evening Alert Snoozed*\n\nThe scheduler would skip the clock-out alert."
    if outcome == "dismissed_morning":
        return "✅ *Morning Alert Dismissed*\n\nThe scheduler would not remind again for this clock-in."
    if outcome == "dismissed_evening":
        return "✅ *Evening Alert Dismissed*\n\nThe scheduler would not remind again for this clock-out."
    if outcome == "waiting_grace_period":
        return "⏳ *Within Grace Period*\n\nNo alert would be sent yet."
    if outcome == "waiting_alert_gap":
        return "⏳ *Waiting Between Reminders*\n\nNo repeat alert would be sent yet."
    if outcome == "morning_success_would_send":
        return "✅ *Morning Clock-In Found*\n\nThe scheduler would confirm clock-in and update morning success."
    if outcome == "evening_success_would_send":
        return "👋 *Evening Clock-Out Found*\n\nThe scheduler would confirm clock-out and update evening success."
    if outcome == "morning_alert_would_send":
        return "🚨 *Morning Alert Would Send*\n\nThe scheduler would send a missed clock-in alert."
    if outcome == "evening_alert_would_send":
        return "🚨 *Evening Alert Would Send*\n\nThe scheduler would send a missed clock-out alert."
    if outcome == "no_action":
        return "ℹ️ *No Action*\n\nThe scheduler would not take an action for this status/window."
    return f"ℹ️ *Simulation Completed*\n\nOutcome: `{outcome}`"


def _format_portal_details(status: AttendanceStatus, details: dict | None) -> str:
    """Summarize what the scraper saw."""
    lines = [f"*Portal status:* `{status.value}`"]
    if details and details.get("clock_in"):
        clock_in = details["clock_in"]
        lines.append(f"*Clock-in:* {clock_in.get('time', 'Unknown')} ({clock_in.get('location', 'Unknown')})")
    if details and details.get("clock_out"):
        clock_out = details["clock_out"]
        lines.append(f"*Clock-out:* {clock_out.get('time', 'Unknown')} ({clock_out.get('location', 'Unknown')})")
    return "\n".join(lines)


def _is_snoozed_for_simulated_date(snooze_until: str | None, current_datetime: datetime) -> bool:
    """Return whether a saved snooze applies to this dry-run date/time."""
    if not snooze_until:
        return False

    try:
        snooze_datetime = datetime.fromisoformat(snooze_until)
    except ValueError:
        return False

    # Snooze is a real, absolute user action. In dry runs, do not let today's
    # snooze affect arbitrary past/future simulated dates.
    if snooze_datetime.date() != current_datetime.date():
        return False

    return current_datetime < snooze_datetime


async def _dry_run_scheduler(user: dict, test_time: time, check_date: date) -> tuple[str, AttendanceStatus | None, dict | None]:
    """Mirror scheduler decisions without sending messages or writing DB state."""
    if is_ethiopian_holiday(check_date):
        return "skipped_holiday", None, None

    working_day_indices = [int(d) for d in user["working_days"].split(",")]
    if check_date.weekday() not in working_day_indices:
        return "skipped_non_working_day", None, None

    start_time = parse_time(user["start_time"])
    end_time = parse_time(user["end_time"])
    if not start_time or not end_time:
        return "error", None, None

    morning_window = test_time >= start_time
    evening_window = test_time >= end_time

    if evening_window:
        check_type = "evening"
        if user.get("last_evening_success_date") == check_date.isoformat():
            return "already_confirmed_evening", None, None
    elif morning_window:
        check_type = "morning"
        if user.get("last_morning_success_date") == check_date.isoformat():
            return "already_confirmed_morning", None, None
    else:
        return "before_shift", None, None

    portal_url = os.getenv("PORTAL_URL", "https://example.com/attendance")
    async with aiohttp.ClientSession() as session:
        status, details = await check_attendance_async(session, user["kaba_id"], check_date, portal_url)

    if status == AttendanceStatus.ERROR:
        return "error", status, details

    current_datetime = datetime.combine(check_date, test_time, tzinfo=EAT)

    if check_type == "morning":
        if status in (AttendanceStatus.CLOCKED_IN, AttendanceStatus.CLOCKED_OUT):
            return "morning_success_would_send", status, details

        if status == AttendanceStatus.NO_RECORD:
            if current_datetime < datetime.combine(check_date, start_time, tzinfo=EAT) + timedelta(minutes=5):
                return "waiting_grace_period", status, details
            if user.get("morning_dismissed_date") == check_date.isoformat():
                return "dismissed_morning", status, details
            if _is_snoozed_for_simulated_date(user.get("morning_snooze_until"), current_datetime):
                return "snoozed_morning", status, details
            if user.get("last_morning_alert_date") == check_date.isoformat():
                last_alert_time = user.get("last_morning_alert_time")
                if last_alert_time:
                    try:
                        if current_datetime < datetime.fromisoformat(last_alert_time) + timedelta(minutes=5):
                            return "waiting_alert_gap", status, details
                    except ValueError:
                        pass
            return "morning_alert_would_send", status, details

    if check_type == "evening":
        if status == AttendanceStatus.CLOCKED_OUT:
            return "evening_success_would_send", status, details

        if status == AttendanceStatus.CLOCKED_IN:
            if current_datetime < datetime.combine(check_date, end_time, tzinfo=EAT) + timedelta(minutes=30):
                return "waiting_grace_period", status, details
            if user.get("evening_dismissed_date") == check_date.isoformat():
                return "dismissed_evening", status, details
            if _is_snoozed_for_simulated_date(user.get("evening_snooze_until"), current_datetime):
                return "snoozed_evening", status, details
            if user.get("last_evening_alert_date") == check_date.isoformat():
                last_alert_time = user.get("last_evening_alert_time")
                if last_alert_time:
                    try:
                        if current_datetime < datetime.fromisoformat(last_alert_time) + timedelta(minutes=5):
                            return "waiting_alert_gap", status, details
                    except ValueError:
                        pass
            return "evening_alert_would_send", status, details

    return "no_action", status, details


async def run_test_simulation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict,
    test_time: time,
    today: date
) -> None:
    """Run a dry-run scheduler simulation for one user at a specific time/date."""
    status_msg = (
        f"🧪 *Developer Dry Run - Simulating {test_time.strftime('%H:%M')}*\n\n"
        f"📅 Date: {today.strftime('%B %d, %Y')}\n"
        f"🆔 Kaba ID: `{user['kaba_id']}`\n"
        f"🕐 Your shift: {user['start_time']} - {user['end_time']}\n\n"
    )

    message = await update.message.reply_text(status_msg + "🔄 Running dry run...", parse_mode="Markdown")
    outcome, portal_status, details = await _dry_run_scheduler(user, test_time, today)

    portal_summary = ""
    if portal_status:
        portal_summary = "\n\n" + _format_portal_details(portal_status, details)

    await message.edit_text(status_msg + _format_outcome(outcome, details) + portal_summary, parse_mode="Markdown")
