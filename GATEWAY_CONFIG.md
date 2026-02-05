# Server-Side Configuration for Gateway

## ✅ Gateway Status: RUNNING
- **IP:** `10.0.52.48`
- **Port:** `8443`
- **URL:** `http://10.0.52.48:8443`

---

## 🔧 Configure Server Bot (2 Steps)

### Step 1: Disable SOCKS5 Proxy

Edit `.env` on the SERVER and **comment out** or remove the SOCKS5 line:

```bash
# SOCKS5 Proxy to bypass Kaspersky
# SOCKS5_PROXY=socks5://proxy.webshare.io:1080  ← Comment this out!
```

### Step 2: Configure bot.py to Use Gateway

In `bot.py` on the SERVER, find the `main()` function (around line 840) and update it to:

```python
def main() -> None:
    """Run the bot."""
    logger.info("KabaGuard bot starting...")
    
    # Build application with gateway URL
    application = (
        Application.builder()
        .token(os.getenv("TELEGRAM_BOT_TOKEN"))
        .base_url("http://10.0.52.48:8443/bot")  # Gateway on your local PC
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .build()
    )
    
    # Add conversation handler
    # ... rest of code stays the same
```

---

## 🚀 Run Bot

After making these changes:

```powershell
# On SERVER
python bot.py
```

**Expected output:**
```
⚠️  SSL verification is DISABLED globally
INFO - KabaGuard bot starting...
INFO - Scheduler started
INFO - Bot is running. Press Ctrl+C to stop.
```

---

## 🔍 What's Happening

```
SERVER (bot.py)  →  YOUR PC (gateway:8443)  →  Telegram API
   ↓ bot command        ↓ relay request           ↓ process
   ← response           ← Telegram response       ← result
```

1. Server connects to YOUR PC (internal network - no Kaspersky block)
2. Your PC relays to Telegram (your PC has normal internet)  
3. Magic! 🎉

---

## ⚠️ Important Notes

- **Keep gateway running** on your LOCAL PC while using the bot
- Gateway must stay on for bot to work
- If you stop gateway, bot will fail
- This is a **temporary development solution** - for production, you'll need IT whitelist

---

## 🐛 Troubleshooting

**If bot still fails:**
1. Check gateway is running: `http://10.0.52.48:8443` should be accessible
2. Check firewall: Windows Firewall might block port 8443
3. Test connection from server:
   ```powershell
   Test-NetConnection -ComputerName 10.0.52.48 -Port 8443
   ```
