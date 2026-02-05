# Network Troubleshooting Guide

## Problem: Kaspersky/Corporate Firewall Blocking Telegram API

Your bot is being blocked with this error:
```
https://api.telegram.org/bot... is not allowed to access 
due to Blocked Cyble Detected URL and IP Addresses rule.
```

This means your corporate network (Kaspersky or similar) is blocking access to Telegram's servers.

---

## Solution Options (in order of preference)

### ✅ Option 1: Request IT Whitelist (RECOMMENDED)

**Contact your IT Security Operation Center** and request whitelisting for:

- **Domain:** `api.telegram.org`
- **Port:** `443` (HTTPS)
- **Protocol:** HTTPS/TLS
- **Justification:** "Official Telegram Bot API required for automated employee attendance monitoring system (KabaGuard). This is a legitimate business tool to improve attendance tracking compliance."

**Benefits:**
- Most secure and reliable
- No performance overhead
- No additional configuration needed

---

### 🔧 Option 2: Configure Corporate Proxy

If your IT department provides a corporate proxy server:

1. **Get proxy details** from IT (example: `http://proxy.company.com:8080`)

2. **Edit `.env` file** and uncomment these lines:
   ```bash
   HTTP_PROXY=http://proxy.company.com:8080
   HTTPS_PROXY=http://proxy.company.com:8080
   ```

3. **Restart the bot:**
   ```bash
   python bot.py
   ```

The proxy settings will be automatically picked up by Python's httpx library.

---

### 🖥️ Option 3: Run on Different Server

Run the bot on a machine that **doesn't have these network restrictions**:

**Option 3A: Your Local Development Machine**
```bash
# On your laptop/desktop
cd c:\Users\nahomel\Documents\Projects\KabaGuard
python bot.py
```

**Option 3B: Cloud Server (Heroku, DigitalOcean, AWS, etc.)**
- Deploy to a cloud platform
- No corporate network restrictions
- Bot runs 24/7

---

### 🧪 Option 4: Test with VPN

If allowed by your company policy:
1. Connect to a personal VPN
2. Run the bot
3. VPN will bypass corporate firewall

**⚠️ Warning:** Check your company's acceptable use policy first!

---

## Testing Network Connectivity

### Test 1: Direct Telegram API Access
```bash
curl https://api.telegram.org/bot8258272597:AAEtXLRuhh0gswm2iIPDBpAfQ8Nr8xz5sa4/getMe
```

**Expected Result if Blocked:**
```html
<!-- Kaspersky block page -->
```

**Expected Result if Working:**
```json
{"ok":true,"result":{"id":8258272597,"is_bot":true,...}}
```

### Test 2: Check Proxy Configuration
```bash
# PowerShell
$env:HTTP_PROXY
$env:HTTPS_PROXY

# CMD
echo %HTTP_PROXY%
echo %HTTPS_PROXY%
```

### Test 3: Test with Python
```python
import os
import httpx

# Test direct connection
try:
    response = httpx.get("https://api.telegram.org/bot<TOKEN>/getMe", verify=False)
    print("✅ Direct connection works:", response.status_code)
except Exception as e:
    print("❌ Direct connection failed:", e)

# Test with proxy
try:
    proxies = {
        "http://": os.getenv("HTTP_PROXY"),
        "https://": os.getenv("HTTPS_PROXY")
    }
    response = httpx.get(
        "https://api.telegram.org/bot<TOKEN>/getMe",
        proxies=proxies,
        verify=False
    )
    print("✅ Proxy connection works:", response.status_code)
except Exception as e:
    print("❌ Proxy connection failed:", e)
```

---

## Current Bot Configuration

Your `.env` file now has proxy support ready:

```bash
# Telegram Bot Token
TELEGRAM_BOT_TOKEN=8258272597:AAEtXLRuhh0gswm2iIPDBpAfQ8Nr8xz5sa4

# SQLite Database Path
DATABASE_PATH=kabaguard.db

# Company Portal URL
PORTAL_URL=http://attendance:1919/

# SSL Settings
DISABLE_SSL_VERIFY=1

# Proxy Settings (optional - uncomment and configure if needed)
# HTTP_PROXY=http://proxy.company.com:8080
# HTTPS_PROXY=http://proxy.company.com:8080
# NO_PROXY=localhost,127.0.0.1
```

**To enable proxy:** Uncomment the proxy lines and set your company's proxy address.

---

## Next Steps

1. **First,** try requesting IT whitelist (most reliable solution)
2. **If that fails,** ask IT for proxy server details
3. **If proxy available,** configure it in `.env`
4. **Last resort:** Run on personal machine or cloud server

---

## Need Help?

If you continue experiencing issues:

1. Check with IT: "Can I access `api.telegram.org` from this server?"
2. Test connectivity using the commands above
3. Review bot logs for specific error messages
4. Consider running the bot from a different location
