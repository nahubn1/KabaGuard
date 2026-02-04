# SSL Certificate Fix for Corporate Networks

## Problem

You're getting this error:
```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain
```

This happens when your network uses a corporate proxy with a self-signed certificate.

## ✅ SOLUTION (Built into the bot!)

The bot now has SSL bypass support built-in. Just add this to your `.env` file:

```env
DISABLE_SSL_VERIFY=1
```

Then run:
```powershell
python bot.py
```

**That's it!** The bot will automatically disable SSL verification.

> [!WARNING]
> This disables SSL certificate verification. Only use for testing in development environments behind corporate proxies. Never use in production!

---

## Alternative Solutions

### Option 1: Use Different Network

Try running the bot on:
- Mobile hotspot
- Home network  
- VPN that bypasses the corporate proxy

### Option 2: Install Corporate CA Certificate

Contact your IT department to get the corporate CA certificate and install it to Windows certificate store.

### Option 3: Use System Certificates

Set this environment variable:

**Windows PowerShell:**
```powershell
$env:SSL_CERT_FILE = "C:\path\to\your\corporate\ca-bundle.crt"
python bot.py
```

**Or add to `.env`:**
```env
SSL_CERT_FILE=C:\path\to\your\corporate\ca-bundle.crt
```
