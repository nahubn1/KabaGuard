# KabaGuard Portal Configuration Guide

## ✅ Scraper Customized

The scraper has been customized based on your portal screenshots. It now:

1. **Submits form data** with ID number and date
2. **Parses the attendance table** with columns: Full Name, ID No, Event, Transaction Date, Location
3. **Detects events**: Looks for "CLOCK_IN" and "CLOCK_OUT" in the Event column
4. **Returns status**:
   - `CLOCKED_OUT` if both CLOCK_IN and CLOCK_OUT are found
   - `CLOCKED_IN` if only CLOCK_IN is found
   - `NO_RECORD` if no records are found

## 🔧 What You Need to Configure

### 1. Portal URL in `.env`

You need to create a `.env` file and set the actual portal URL. Copy from `.env.example`:

```bash
cp .env.example .env
```

Then edit `.env` and set:

```env
PORTAL_URL=https://your-actual-kaba-portal-url.com/attendance.php
```

**Important**: Replace with the actual URL you use to access the Kaba attendance portal.

### 2. Bot Token

Get your bot token from [@BotFather](https://t.me/BotFather) on Telegram and add it to `.env`:

```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
```

### 3. Test the Scraper (Optional)

The scraper might need minor adjustments depending on:
- **Form field names**: Currently using `id_number` and `pick_date`
- **HTTP method**: Tries POST first, falls back to GET
- **Table structure**: Assumes Event is in the 3rd column (index 2)

If the bot doesn't work correctly, we can test the scraper directly and adjust these parameters.

## 📝 Current Form Data

Based on your screenshots, the scraper submits:

```python
form_data = {
    'id_number': kaba_id,      # Your Kaba ID (e.g., "30185")
    'pick_date': formatted_date # Date in MM/DD/YYYY format
}
```

## 🧪 How to Test

Once configured:

1. Run the bot: `python bot.py`
2. Register yourself via `/register`
3. Wait for the 5-minute scheduler to run
4. Check the logs to see if scraping works

### If Scraping Fails

Check the logs for errors. Common issues:
- Wrong form field names
- Wrong HTTP method
- Authentication required
- Different table structure

We can adjust the scraper code based on the error messages.

## 📸 Portal Screenshots Reference

![Portal Form](C:/Users/nahomel/.gemini/antigravity/brain/547b5604-8667-4d0a-9aa9-4f9ccf0bc690/uploaded_image_0_1770189925606.png)

![Portal Results](C:/Users/nahomel/.gemini/antigravity/brain/547b5604-8667-4d0a-9aa9-4f9ccf0bc690/uploaded_image_1_1770189925606.png)

The scraper is based on these screenshots showing:
- Input form with ID Number and Pick Date
- "View My Attendance" button
- Results table with CLOCK_IN and CLOCK_OUT events
