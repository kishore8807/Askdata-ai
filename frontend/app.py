"""Flask front door: serves the Stitch UI and reverse-proxies /api/* to the FastAPI backend.

Serving UI and API from ONE origin lets the session cookie stay HttpOnly + SameSite=Strict
(no CORS, no tokens in JavaScript, no localStorage).

Run (dev):   python frontend/app.py
Run (prod):  gunicorn -w 2 -b 0.0.0.0:5000 frontend.app:app
"""
from __future__ import annotations

import os
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Response, render_template, request

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "5"))
PROXY_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")) + 15

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = (MAX_UPLOAD_MB + 1) * 1024 * 1024
_session = requests.Session()           # connection reuse to the backend
# SECURITY: the proxy must be completely stateless. A requests.Session normally stores Set-Cookie values and
# replays them on later requests, which would let ONE user's session authenticate EVERY visitor.
_session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))   # accept no cookies, ever
_session.trust_env = False

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "content-encoding", "content-length", "host"}
FORWARD_REQUEST_HEADERS = ("content-type", "cookie", "x-csrf-token", "accept", "user-agent")

CSP = ("default-src 'self'; "
       "script-src 'self' https://cdn.tailwindcss.com 'unsafe-eval'; "      # Tailwind Play CDN compiles in-browser
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@app.after_request
def headers(resp: Response) -> Response:
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if resp.mimetype == "text/html":
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/")
def index():
    return render_template("index.html")


@app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy(path: str):
    headers_out = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQUEST_HEADERS}
    headers_out["X-Forwarded-For"] = request.remote_addr or ""
    try:
        upstream = _session.request(
            request.method, f"{BACKEND_URL}/api/{path}", params=request.args, data=request.get_data(),
            headers=headers_out, timeout=PROXY_TIMEOUT, allow_redirects=False, stream=False, cookies=None)
    except requests.RequestException:
        return Response('{"success": false, "message": "The service is temporarily unavailable. Please try again later."}',
                        status=502, mimetype="application/json")
    resp = Response(upstream.content, status=upstream.status_code)
    for k, v in upstream.headers.items():
        if k.lower() not in HOP_BY_HOP and k.lower() != "set-cookie":
            resp.headers[k] = v
    for cookie in upstream.raw.headers.getlist("Set-Cookie"):       # preserve multiple Set-Cookie headers
        resp.headers.add("Set-Cookie", cookie)
    return resp


@app.errorhandler(413)
def too_large(_):
    return Response(f'{{"success": false, "message": "The file is too large (maximum {MAX_UPLOAD_MB} MB)."}}',
                    status=413, mimetype="application/json")


if __name__ == "__main__":
    app.run(host=os.getenv("FRONTEND_HOST", "127.0.0.1"), port=int(os.getenv("FRONTEND_PORT", "5000")), debug=False)
