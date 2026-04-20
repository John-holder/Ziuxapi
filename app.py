import os
import secrets
import requests
import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from .runner import __version__
import jwt
try:
    from jwt import PyJWT
except ImportError:
    raise SystemExit(1)

load_dotenv()
raw_admins = os.environ.get("ZIUX_AUTH_MODS", "")
WHITELIST_ADMINS = [ip.strip() for ip in raw_admins.split(",") if ip.strip()]
PASSWORD_HASH = os.environ.get("ZIUX_PASSWORD_HASH")
JWT_SECRET = os.environ.get("JWT_SECRET")
CANVAS_API_KEY = os.environ.get("CANVAS_API_KEY")
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL")

if not all([PASSWORD_HASH, CANVAS_API_KEY, CANVAS_BASE_URL, WHITELIST_ADMINS, JWT_SECRET]):
    print("Ziux cannot continue with missing environment variables!")
    raise SystemExit(1)

class SecuritySession:
    def __init__(self):
        self._jwtsessions = {}
        self._blacklistedips = set()

    def new_session(self, ip):
        if self.check_existing_session(ip):
            return None, "Session exists"

        if ip in self._blacklistedips:
            return None, "Restricted"

        session_hash = secrets.token_hex(16)

        payload = {
            "ip": ip,
            "authentication": session_hash,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=10),
            "iat": datetime.datetime.utcnow()
        }

        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        self._jwtsessions[ip] = session_hash
        return token, "Created"

    def check_existing_session(self, ip):
        return ip in self._jwtsessions and self._jwtsessions[ip] is not None

    def remove_session(self, ip, blacklist_ip=False):
        if blacklist_ip:
            self._blacklistedips.add(ip)

        if ip in self._jwtsessions:
            del self._jwtsessions[ip]

    def verify_session(self, ip, session):
        if ip in self._blacklistedips:
            return False

        owner_hash = self._jwtsessions.get(ip)
        if not owner_hash:
            return False

        try:
            decoded = jwt.decode(session, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return False
        except jwt.InvalidTokenError:
            return False

        if decoded.get("ip") != ip:
            return False

        if decoded.get("authentication") != owner_hash:
            return False

        return True


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

security = SecuritySession()

CANVAS_HEADERS = {
    "Authorization": f"Bearer {CANVAS_API_KEY}"
}

def get_canvas(path: str):
    url = f"{CANVAS_BASE_URL}{path}"
    response = requests.get(url, headers=CANVAS_HEADERS, timeout=(3.05, 10))
    response.raise_for_status()
    return response.json()


def verifyJWT(token):
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return decoded.get("Authentication") == PASSWORD_HASH
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False

def require_auth():
    request_ip = request.remote_addr
    session_token = request.headers.get("Authorization")

    if not session_token:
        return False

    # Support "Bearer <token>" or raw token
    if session_token.startswith("Bearer "):
        session_token = session_token.split(" ", 1)[1].strip()

    return security.verify_session(request_ip, session_token)

@app.get("/")
def index():
    return jsonify({
        "result": f"Welcome to Ziux API. Running on {__version__}."
    })

def authenticate():
    client_token = request.headers.get("Authentication")
    request_ip = request.remote_addr

    if not client_token:
        return jsonify({"result": "Missing Authentication header"}), 400

    if not verifyJWT(client_token):
        return jsonify({"result": "Invalid client token"}), 401

    session_token, status = security.new_session(request_ip)

    if status == "Restricted":
        return jsonify({"result": "IP restricted"}), 403

    if status == "Session exists":
        return jsonify({"result": "Session already exists"}), 409

    return jsonify({
        "result": "Authenticated",
        "session_token": session_token
    })

def authenticate_admin(remote_addr, admin_token):
    if remote_addr not in WHITELIST_ADMINS:
        return False

    if not admin_token:
        return False

    return verifyJWT(admin_token)

@app.get("/a/generate/userkey")
def userkey():
    request_ip = request.remote_addr
    admin_token = request.headers.get("Authentication")

    if not authenticate_admin(request_ip, admin_token):
        return jsonify({"result": "Unauthorized"}), 401

    user_ip = request.args.get("ip")
    if not user_ip:
        return jsonify({"result": "ip is required"}), 400

    session_token, status = security.new_session(user_ip)

    if status == "Restricted":
        return jsonify({"result": "Target IP is restricted"}), 403
    if status == "Session exists":
        return jsonify({"result": "Target IP already has a session"}), 409

    return jsonify({
        "result": "User session created",
        "ip": user_ip,
        "session_token": session_token
    })

@app.post("/a/revoke/userkey")
def removeuserkey():
    request_ip = request.remote_addr
    admin_token = request.headers.get("Authentication")

    if not authenticate_admin(request_ip, admin_token):
        return jsonify({"result": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    target_ip = data.get("ip")
    blacklist_ip = bool(data.get("blacklist_ip", False))

    if not target_ip:
        return jsonify({"result": "ip is required"}), 400

    security.remove_session(target_ip, blacklist_ip=blacklist_ip)

    return jsonify({
        "result": "Security session reset",
        "ip": target_ip,
        "blacklisted": blacklist_ip
    })

@app.get("/authenticate/link/client")
def authenticate_client():
    return authenticate()

@app.get("/edu/get/me")
def get_me():
    if not require_auth():
        return jsonify({"result": "Unauthorized"}), 401

    try:
        return jsonify(get_canvas("/users/self"))
    except requests.RequestException as e:
        return jsonify({"result": "Canvas request failed", "error": str(e)}), 502

@app.get("/edu/get/courses")
def get_courses():
    if not require_auth():
        return jsonify({"result": "Unauthorized"}), 401

    try:
        return jsonify(get_canvas("/courses"))
    except requests.RequestException as e:
        return jsonify({"result": "Canvas request failed", "error": str(e)}), 502

@app.get("/edu/get/assignments")
def get_assignments():
    if not require_auth():
        return jsonify({"result": "Unauthorized"}), 401

    course_id = request.args.get("course_id")
    if not course_id:
        return jsonify({"result": "course_id is required"}), 400

    try:
        return jsonify(get_canvas(f"/courses/{course_id}/assignments"))
    except requests.RequestException as e:
        return jsonify({"result": "Canvas request failed", "error": str(e)}), 502

@app.get("/edu/get/quizzes")
def get_quizzes():
    if not require_auth():
        return jsonify({"result": "Unauthorized"}), 401

    course_id = request.args.get("course_id")
    if not course_id:
        return jsonify({"result": "course_id is required"}), 400

    try:
        return jsonify(get_canvas(f"/courses/{course_id}/quizzes"))
    except requests.RequestException as e:
        return jsonify({"result": "Canvas request failed", "error": str(e)}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
