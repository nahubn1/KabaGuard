"""
Test script to verify SSL bypass works with httpx
"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def test_ssl_bypass():
    """Test if SSL bypass works."""
    print("Testing SSL bypass...")
    
    # Create client with SSL verification disabled
    async with httpx.AsyncClient(verify=False) as client:
        try:
            # Test connection to Telegram API
            response = await client.get("https://api.telegram.org")
            print(f"✅ SUCCESS! Status: {response.status_code}")
            print(f"Response preview: {response.text[:100]}...")
            return True
        except Exception as e:
            print(f"❌ FAILED: {e}")
            return False

if __name__ == "__main__":
    result = asyncio.run(test_ssl_bypass())
    if result:
        print("\n✅ SSL bypass is working! The bot should work.")
    else:
        print("\n❌ SSL bypass failed. There may be a deeper network issue.")
