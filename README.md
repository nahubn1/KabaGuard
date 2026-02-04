# KabaGuard Bot

Production-ready Telegram bot for tracking employee attendance on a slow company portal.

## Features

- ✅ **Async Architecture**: Built with `python-telegram-bot`, `aiohttp`, and `aiosqlite`
- 🚀 **DB-First Optimization**: Checks local database before scraping to minimize slow network requests
- ⏰ **Smart Scheduling**: Runs every 5 minutes with context filtering (holidays, working days, DB state)
- 🌍 **Ethiopian Timezone Support**: Configured for EAT/UTC+3
- 📅 **Holiday Awareness**: Automatically skips Ethiopian holidays
- 🔔 **Intelligent Alerts**: Notifies users if they miss clock-in (10 min grace) or clock-out (25 min grace)

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your details:

```bash
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=your_actual_token_from_botfather
DATABASE_PATH=kabaguard.db
PORTAL_URL=https://your-company-portal.com/attendance
```

### 3. Customize Portal Scraper

**IMPORTANT**: Open `scraper.py` and update the HTML parsing logic:

- Update the `url` format in `check_attendance_async()` to match your portal's API
- Update the CSS selectors (`soup.find()` calls) to match your portal's HTML structure
- Test with real credentials to ensure status detection works

### 4. Run the Bot

```bash
python bot.py
```

## Usage

### User Commands

- `/start` - Welcome message and introduction
- `/register` - Set up your schedule (Kaba ID, shift times, working days)
- `/status` - View your current registration details
- `/check YYYY-MM-DD` - Test scraper for a specific date (debugging)
- `/help` - Show help message

### Registration Flow

1. **Kaba ID**: Enter your company portal ID
2. **Shift Start Time**: Enter in HH:MM format (e.g., `08:00`)
3. **Shift End Time**: Enter in HH:MM format (e.g., `17:00`)
4. **Working Days**: Select from keyboard or type (e.g., `Monday,Tuesday,Friday` or `Mon-Fri`)
5. **Confirm**: Review and confirm your details

## How It Works

### Scheduler Optimization (DB-First)

The bot runs every **5 minutes** and follows this flow for each user:

**Step 1: Context Filters** (Fastest)
- Is today an Ethiopian holiday? → Skip
- Is today a working day for user? → Skip

**Step 2: DB State Check** (Fast)
- Morning: Already sent success/alert today? → Skip
- Evening: Already sent success/alert today? → Skip

**Step 3: Conditional Scrape** (Slow - only if needed)
- Fetch attendance status from portal

**Step 4: Decision & Action**
- **Morning**: 
  - `CLOCKED_IN` → Send success message, update DB
  - `NO_RECORD` + 10 mins late → Send alert, update DB
- **Evening**:
  - `CLOCKED_OUT` → Send success message, update DB
  - `CLOCKED_IN` + 25 mins late → Send alert, update DB

## Project Structure

```
KabaGuard/
├── bot.py              # Main application (commands, scheduler, logic)
├── db.py               # Async database operations
├── scraper.py          # Async web scraping
├── requirements.txt    # Python dependencies
├── .env.example        # Environment template
└── README.md           # This file
```

## Database Schema

The `users` table stores:
- `user_id` - Telegram user ID (primary key)
- `kaba_id` - Company portal ID
- `start_time` - Shift start (HH:MM)
- `end_time` - Shift end (HH:MM)
- `working_days` - Comma-separated day indices (0=Mon, 6=Sun)
- `last_morning_success_date` - Last successful clock-in notification
- `last_morning_alert_date` - Last missed clock-in alert
- `last_evening_success_date` - Last successful clock-out notification
- `last_evening_alert_date` - Last missed clock-out alert

## Timezone Configuration

The bot uses **EAT/UTC+3** (Africa/Addis_Ababa) for all time operations. The scheduler and all time comparisons respect this timezone automatically.

## Notes

- The scraper uses placeholder HTML parsing - **you must customize it** for your actual portal
- Holidays are detected using `holidays.Ethiopia()` - verify this matches your company calendar
- Network errors during scraping are handled gracefully and won't trigger false alerts
- The DB-first approach significantly reduces load on slow portals

## License

MIT
