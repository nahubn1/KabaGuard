"""
Robust Async HTTP gateway for Telegram Bot API
Runs on an external/unrestricted server to relay requests for the restricted server.
"""
import os
import logging
from aiohttp import web
import aiohttp

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org"

async def proxy_handler(request):
    """Handles routing the incoming request to the actual Telegram API"""
    path = request.match_info.get('proxy_path', '')
    url = f"{TELEGRAM_API_URL}/{path}"
    
    # Extract query params and HTTP method
    params = dict(request.query)
    method = request.method
    
    logger.info(f"Relaying {method} request to: {url}")
    
    # Read body if it's a POST/PATCH request
    data = None
    if request.can_read_body:
        data = await request.read()
        
    # We must use a large timeout because getUpdates long-polling holds connections open for 50s
    timeout = aiohttp.ClientTimeout(total=80) 
    
    # Forward the headers (except Hop-by-Hop headers which aiohttp handles)
    headers = {k: v for k, v in request.headers.items() 
               if k.lower() not in ('host', 'content-length', 'connection', 'transfer-encoding')}

    try:
        # Disable SSL verification in case the gateway environment has missing CA certs
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                headers=headers
            ) as response:
                
                response_data = await response.read()
                
                # Relay the response stream back to the original client
                return web.Response(
                    body=response_data,
                    status=response.status,
                    content_type=response.headers.get('Content-Type', 'application/json')
                )
                
    except Exception as e:
        logger.error(f"Error relaying request: {str(e)}")
        return web.json_response({"ok": False, "error_code": 502, "description": f"Bad Gateway: {str(e)}"}, status=502)

app = web.Application()
# Match any path starting with /bot or /file/bot (for file downloads)
app.router.add_route('*', '/{proxy_path:.*}', proxy_handler)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8443))
    logger.info(f"🌐 Async Telegram Gateway starting on port {port}")
    logger.info(f"Configure your restricted bot to use: http://YOUR_GATEWAY_IP:{port}/bot")
    web.run_app(app, host='0.0.0.0', port=port)