"""
Simple HTTP gateway for Telegram Bot API
Runs on local PC to relay requests to Telegram
"""
from flask import Flask, request, Response
import requests

app = Flask(__name__)
TELEGRAM_API = "https://api.telegram.org"

@app.route('/bot<token>/<method>', methods=['GET', 'POST'])
def proxy(token, method):
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    
    if request.method == 'POST':
        resp = requests.post(url, json=request.get_json(), timeout=30)
    else:
        resp = requests.get(url, params=request.args, timeout=30)
    
    return Response(resp.content, status=resp.status_code, 
                    content_type=resp.headers['Content-Type'])

if __name__ == '__main__':
    print("🌐 Telegram Gateway running on http://localhost:8443")
    print("Configure bot to use: http://YOUR_PC_IP:8443")
    app.run(host='0.0.0.0', port=8443)