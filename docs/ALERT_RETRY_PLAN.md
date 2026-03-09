# Alert Retry System - Implementation Plan

## 📋 Requirements

### 1. Confirmation After Alert
**Current behavior:** If alert is sent, no confirmation sent when user clocks in/out later  
**New behavior:** Always send confirmation when user actually clocks in/out, even if alert was sent

### 2. Clock-Out Alert Retries
**Rule:** Send up to 3 alerts with minimum 15-minute gaps between each

---

## 🗄️ Database Schema Changes

### New Columns Needed:

```sql
ALTER TABLE users ADD COLUMN last_evening_alert_count INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN last_evening_alert_time TEXT;
ALTER TABLE users ADD COLUMN last_morning_alert_count INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN last_morning_alert_time TEXT;
```

**Purpose:**
- `alert_count`: Track how many alerts sent today (reset daily)
- `alert_time`: Track when last alert was sent (for 15-min gap check)

---

## 🔄 Logic Changes

### Change 1: Remove Alert Check from Skip Logic

**Current (lines 680-682):**
```python
if user['last_evening_alert_date'] == today.isoformat():
    logger.debug(f"User {user_id}: Already alerted for clock-out today")
    return  # ← SKIP checking
```

**New:**
```python
# Remove this check entirely - always check and confirm if clocked out
```

### Change 2: Implement 3-Retry Logic

**New Evening Alert Handler:**
```python
async def should_send_evening_alert(user: dict, today: date, current_time: datetime) -> bool:
    """Check if we should send another evening alert."""
    
    # Reset count if it's a new day
    if user['last_evening_alert_date'] != today.isoformat():
        return True  # First alert of the day
    
    # Check if we've sent 3 alerts already
    alert_count = user.get('last_evening_alert_count', 0)
    if alert_count >= 3:
        logger.info(f"User {user['user_id']}: Max 3 alerts sent, stopping")
        return False
    
    # Check if 15 minutes have passed since last alert
    last_alert_time_str = user.get('last_evening_alert_time')
    if last_alert_time_str:
        last_alert_time = datetime.fromisoformat(last_alert_time_str)
        time_since_last = (current_time - last_alert_time).total_seconds() / 60
        
        if time_since_last < 15:
            logger.debug(f"User {user['user_id']}: Only {time_since_last:.1f}min since last alert")
            return False
    
    return True
```

---

## 📝 DB Update Functions Needed

```python
async def update_evening_alert_with_retry(
    self, 
    user_id: int, 
    alert_date: date, 
    alert_time: datetime,
    increment_count: bool = True
) -> None:
    """Update evening alert with retry tracking."""
    async with aiosqlite.connect(self.db_path) as db:
        if increment_count:
            # Increment count
            await db.execute(
                "UPDATE users SET "
                "last_evening_alert_date = ?, "
                "last_evening_alert_time = ?, "
                "last_evening_alert_count = last_evening_alert_count + 1 "
                "WHERE user_id = ?",
                (alert_date.isoformat(), alert_time.isoformat(), user_id)
            )
        else:
            # Reset count for new day
            await db.execute(
                "UPDATE users SET "
                "last_evening_alert_date = ?, "
                "last_evening_alert_time = ?, "
                "last_evening_alert_count = 1 "
                "WHERE user_id = ?",
                (alert_date.isoformat(), alert_time.isoformat(), user_id)
            )
        await db.commit()
```

---

## 🎯 Implementation Steps

1. **Update DB schema** - Add new columns
2. **Modify `check_user_attendance`** - Remove alert skip logic
3. **Add retry checker function** - Implement 15-min + 3-max logic
4. **Update `handle_evening_check`** - Use retry logic
5. **Update DB functions** - Add retry tracking
6. **Reset counts** - When new day detected, reset count to 0

---

## ✅ Expected Behavior

### Scenario 1: User Forgets, Then Remembers
```
17:00 - Shift ends
17:25 - Alert #1: "Please clock out!" (25 min late)
17:40 - Alert #2: "STILL not clocked out!" (15 min after alert #1)
17:50 - User clocks out
17:55 - Scheduler runs → "Clock-out confirmed!" ✅ (even though alerts were sent)
```

### Scenario 2: User Never Clocks Out
```
17:00 - Shift ends
17:25 - Alert #1
17:40 - Alert #2  
17:55 - Alert #3
18:10 - No more alerts (max 3 reached)
```

### Scenario 3: User Clocks Out on Time
```
17:00 - Shift ends, user clocks out
17:05 - Scheduler runs → "Clock-out confirmed!" ✅
17:10 - Scheduler runs → Skip (already confirmed)
```
