# New Alert & Retry System

## 🚀 Key Improvements

### 1. "Check-In" Confirmation Always Sent
**Old Behavior:** If the bot sent you an alert ("You forgot to clock out!"), it would STOP checking. When you finally clocked out, you got **no confirmation**.
**New Behavior:** The bot sends the alert but **continues checking**. When you finally clock out, you will receive:
> "👋 Clock-Out Confirmed!"

### 2. Smart Retry System (Evening Only)
**Old Behavior:** Only **1 alert** sent 25 minutes after your shift end.
**New Behavior:**
- **Max Alerts:** Up to **3 alerts** per day
- **Interval:** Minimum **15 minutes** between alerts
- **Reset:** Count resets automatically next day

## 📝 Example Scenarios

### Scenario A: You Forget to Clock Out
- **17:00** - Shift Ends
- **17:25** - 🚨 **Alert #1** ("Please clock out!")
- **17:40** - 🚨 **Alert #2** ("Still not clocked out!")
- **17:55** - 🚨 **Alert #3** (Final warning)
- **18:10** - (No more alerts to avoid spamming)

### Scenario B: You Get Alert, Then Clock Out
- **17:25** - 🚨 **Alert #1**
- **17:35** - You go to portal and clock out 🏃‍♂️
- **17:35-17:40** - Bot checks portal
- **17:40** - 👋 **"Clock-Out Confirmed!"** (You finally get your confirmation!)

## 🔧 Troubleshooting

If you suspect the alerts aren't working:
1. Check the logs for: `Sending evening alert #X`
2. Check logs for: `Only X min since last alert, waiting`
3. Ensure your `end_time` is set correctly in settings

**Note:** The morning (Clock-In) logic is unchanged: 1 alert after 10 minutes grace period.
