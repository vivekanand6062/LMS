import os
import sys
import time
import datetime
import uuid
import json
import threading
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests

# Add root directory to sys.path to access models
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models import (
    generate_id, hash_password, verify_password,
    calculate_fine, calculate_routing_score
)

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
DATA_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
CORS(app)

SERVER_A_URL = os.environ.get("SERVER_A_URL", "http://localhost:5001")
SERVER_B_URL = os.environ.get("SERVER_B_URL", "http://localhost:5002")

# Backend servers configuration and dynamic state
backend_servers = {
    "server_a": {
        "id": "server_a",
        "name": "Server A",
        "url": SERVER_A_URL,
        "port": 5001,
        "genres": ["Fiction", "Fantasy", "Adventure"],
        "status": "HEALTHY",  # HEALTHY, OFFLINE, RECOVERED
        "active_requests": 0,
        "total_requests": 0,
        "latency_ms": 12.0,
        "cpu_percent": 5.0,
        "books_count": 5,
        "last_health_check": None,
        "last_successful_response": None,
        "failure_detected_at": None,
        "recovery_detected_at": None,
        "failure_count": 0,
        "consecutive_failures": 0,
        "simulated_offline": False
    },
    "server_b": {
        "id": "server_b",
        "name": "Server B",
        "url": SERVER_B_URL,
        "port": 5002,
        "genres": ["Science", "Technology", "Math"],
        "status": "HEALTHY",  # HEALTHY, OFFLINE, RECOVERED
        "active_requests": 0,
        "total_requests": 0,
        "latency_ms": 14.0,
        "cpu_percent": 4.0,
        "books_count": 5,
        "last_health_check": None,
        "last_successful_response": None,
        "failure_detected_at": None,
        "recovery_detected_at": None,
        "failure_count": 0,
        "consecutive_failures": 0,
        "simulated_offline": False
    }
}

# Distributed system request audit logs
logs = []
LOGS_FILE = os.path.join(DATA_DIR, "gateway_logs.json")

# Issue/Return transaction records
issue_records = []
ISSUES_FILE = os.path.join(DATA_DIR, "issue_records.json")

# User management (In-memory array with JSON persistence, no external database)
users = []
USERS_FILE = os.path.join(DATA_DIR, "users.json")
active_tokens = {}  # token -> user dict

DEFAULT_USERS = [
    {
        "id": "usr-admin-1",
        "username": "admin",
        "password_hash": hash_password("admin123"),
        "role": "admin",
        "name": "Librarian Admin",
        "email": "admin@library.edu"
    },
    {
        "id": "usr-student-1",
        "username": "student",
        "password_hash": hash_password("student123"),
        "role": "student",
        "name": "Alex Student",
        "email": "student@library.edu"
    },
    {
        "id": "usr-student-2",
        "username": "john",
        "password_hash": hash_password("john123"),
        "role": "student",
        "name": "John Doe",
        "email": "john@library.edu"
    }
]

def load_data():
    global users, issue_records, logs
    # Users
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                users = json.load(f)
        except Exception:
            users = [dict(u) for u in DEFAULT_USERS]
    else:
        users = [dict(u) for u in DEFAULT_USERS]
        save_users()

    # Issue records
    if os.path.exists(ISSUES_FILE):
        try:
            with open(ISSUES_FILE, "r", encoding="utf-8") as f:
                issue_records = json.load(f)
        except Exception:
            issue_records = []
    else:
        # Pre-seed with existing book issued to student for initial test
        issue_records = [
            {
                "id": "iss-001",
                "book_id": "bk-a-03",
                "book_title": "The Lord of the Rings",
                "username": "student",
                "issue_date": (datetime.date.today() - datetime.timedelta(days=5)).isoformat(),
                "due_date": (datetime.date.today() + datetime.timedelta(days=9)).isoformat(),
                "return_date": None,
                "fine_amount": 0.0,
                "status": "Issued"
            }
        ]
        save_issues()

    # Logs
    if os.path.exists(LOGS_FILE):
        try:
            with open(LOGS_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []

def save_users():
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2)
    except Exception as e:
        print(f"[Gateway] Error saving users: {e}")

def save_issues():
    try:
        with open(ISSUES_FILE, "w", encoding="utf-8") as f:
            json.dump(issue_records, f, indent=2)
    except Exception as e:
        print(f"[Gateway] Error saving issues: {e}")

def save_logs():
    try:
        # Keep last 500 logs
        with open(LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump(logs[-500:], f, indent=2)
    except Exception as e:
        print(f"[Gateway] Error saving logs: {e}")

def log_request(request_id, action, server, status, routing_score=None, reason="standard", duration_ms=None):
    entry = {
        "request_id": request_id,
        "action": action,
        "server": server,
        "status": status,
        "routing_score": routing_score,
        "reason": reason,
        "duration_ms": duration_ms,
        "timestamp": datetime.datetime.now().isoformat()
    }
    logs.insert(0, entry)
    if len(logs) > 500:
        logs.pop()
    # Async save to avoid slowing down requests
    threading.Thread(target=save_logs, daemon=True).start()

# ---------------- HEALTH MONITOR & FAULT DETECTOR ---------------- #

def background_health_monitor():
    """
    Continuous background health checker and failure detector.
    Pings backend servers every 3 seconds to monitor health, calculate metrics,
    and trigger automatic failover and recovery.
    """
    while True:
        try:
            for s_key, s_data in backend_servers.items():
                if s_data.get("simulated_offline", False):
                    # Manual simulation of server failure
                    if s_data["status"] != "OFFLINE":
                        s_data["status"] = "OFFLINE"
                        s_data["failure_detected_at"] = datetime.datetime.now().isoformat()
                        s_data["failure_count"] += 1
                        print(f"[Gateway Monitor] Simulated outage on {s_data['name']}: marked OFFLINE")
                    continue

                now_str = datetime.datetime.now().isoformat()
                s_data["last_health_check"] = now_str
                try:
                    start_t = time.time()
                    resp = requests.get(f"{s_data['url']}/health", timeout=2.5)
                    ping_lat = round((time.time() - start_t) * 1000.0, 2)

                    if resp.status_code == 200:
                        data = resp.json()
                        s_data["active_requests"] = data.get("active_requests", 0)
                        s_data["total_requests"] = data.get("total_requests", 0)
                        s_data["latency_ms"] = data.get("latency_ms", ping_lat)
                        s_data["cpu_percent"] = data.get("cpu_percent", 5.0)
                        s_data["books_count"] = data.get("books_count", 0)
                        s_data["last_successful_response"] = now_str
                        s_data["consecutive_failures"] = 0

                        # Check recovery transition
                        if s_data["status"] == "OFFLINE":
                            s_data["status"] = "RECOVERED"
                            s_data["recovery_detected_at"] = now_str
                            print(f"[Gateway Monitor] {s_data['name']} has RECOVERED and is back in eligible pool!")
                            log_request(
                                str(uuid.uuid4())[:8],
                                f"HEALTH MONITOR: {s_data['name']} RECOVERED",
                                s_data["url"],
                                "success",
                                reason="server_recovery"
                            )
                        elif s_data["status"] == "RECOVERED":
                            # Return to steady state HEALTHY after 1 successful cycle
                            s_data["status"] = "HEALTHY"
                    else:
                        raise requests.exceptions.RequestException(f"HTTP {resp.status_code}")

                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException) as e:
                    s_data["consecutive_failures"] += 1
                    if s_data["status"] != "OFFLINE":
                        s_data["status"] = "OFFLINE"
                        s_data["failure_detected_at"] = now_str
                        s_data["failure_count"] += 1
                        print(f"[Gateway Monitor] Failure detected on {s_data['name']} ({e}): marked OFFLINE")
                        log_request(
                            str(uuid.uuid4())[:8],
                            f"HEALTH MONITOR: {s_data['name']} FAILED",
                            s_data["url"],
                            "error",
                            reason="health_check_failed"
                        )
        except Exception as e:
            print(f"[Gateway Monitor] Monitor loop error: {e}")

        time.sleep(3.0)

# Start health monitor daemon
monitor_thread = threading.Thread(target=background_health_monitor, daemon=True)
monitor_thread.start()

# ---------------- DYNAMIC ROUTER & LOAD BALANCER ---------------- #

def select_best_server(genre=None, read_only=False):
    """
    Dynamic server selection algorithm.
    1. Identify eligible healthy/recovered servers.
    2. Respect data partitioning (primary genre server) when possible.
    3. Evaluate server load metrics & routing score.
    4. Automatically trigger failover if primary is down.
    
    Returns: (selected_server_dict, reason, score)
    """
    eligible = [
        s for s in backend_servers.values()
        if s["status"] in ("HEALTHY", "RECOVERED") and not s.get("simulated_offline", False)
    ]

    if not eligible:
        return None, "no_healthy_servers", None

    # Calculate scores for all eligible servers
    scores = {}
    for s in eligible:
        metrics = {
            "active_requests": s["active_requests"],
            "latency_ms": s["latency_ms"],
            "cpu_percent": s["cpu_percent"],
            "total_requests": s["total_requests"]
        }
        scores[s["id"]] = calculate_routing_score(metrics)

    # 1. Check Primary Partition if genre is specified
    primary_server = None
    if genre:
        for s in backend_servers.values():
            if genre in s["genres"]:
                primary_server = s
                break

    if primary_server:
        # If primary server is eligible
        if primary_server in eligible:
            # Check if primary server is not critically overloaded compared to alternative
            other_servers = [s for s in eligible if s["id"] != primary_server["id"]]
            primary_score = scores[primary_server["id"]]

            if other_servers:
                min_other_score = min(scores[s["id"]] for s in other_servers)
                # If primary is heavily congested (e.g. score > 2.5x of alternative) on read requests,
                # dynamic load balancing can offload reads to least loaded server!
                if read_only and primary_score > (min_other_score * 2.2):
                    best_alt = min(other_servers, key=lambda s: scores[s["id"]])
                    return best_alt, "load_shift_congested_primary", scores[best_alt["id"]]

            return primary_server, "primary_partition_healthy", primary_score
        else:
            # Primary server is OFFLINE -> AUTOMATIC FAILOVER!
            least_loaded = min(eligible, key=lambda s: scores[s["id"]])
            return least_loaded, f"failover_primary_{primary_server['id']}_offline", scores[least_loaded["id"]]

    # 2. General server selection: Choose least loaded eligible server
    best_server = min(eligible, key=lambda s: scores[s["id"]])
    return best_server, "least_loaded_eligible", scores[best_server["id"]]

# ---------------- AUTHENTICATION HELPERS ---------------- #

def get_current_user():
    """Extract and validate user from Authorization header or cookie or query param."""
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    elif "token" in request.cookies:
        token = request.cookies.get("token")
    elif "token" in request.args:
        token = request.args.get("token")

    if token and token in active_tokens:
        return active_tokens[token]
    return None

def login_required(role=None):
    """Decorator to enforce authentication and optional role."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Unauthorized: Please log in"}), 401
            if role and user.get("role") != role:
                return jsonify({"error": f"Forbidden: Requires {role} privileges"}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ---------------- AUTH API ROUTES ---------------- #

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    user = next((u for u in users if u["username"].lower() == username.lower()), None)
    if not user or not verify_password(password, user["password_hash"]):
        return jsonify({"error": "Invalid username or password"}), 401

    token = str(uuid.uuid4())
    user_payload = {
        "id": user["id"],
        "username": user["username"],
        "name": user["name"],
        "role": user["role"],
        "email": user["email"]
    }
    active_tokens[token] = user_payload

    log_request(str(uuid.uuid4())[:8], f"USER LOGIN: {username} ({user['role']})", "Gateway", "success", reason="auth_login")

    return jsonify({
        "message": f"Welcome back, {user['name']}!",
        "token": token,
        "user": user_payload
    })

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    name = data.get("name", "").strip() or username
    email = data.get("email", "").strip() or f"{username}@library.edu"
    role = data.get("role", "student").strip().lower()

    if role not in ("student", "admin"):
        role = "student"

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    if any(u["username"].lower() == username.lower() for u in users):
        return jsonify({"error": "Username already exists"}), 409

    new_user = {
        "id": generate_id(prefix="usr"),
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "name": name,
        "email": email
    }
    users.append(new_user)
    save_users()

    token = str(uuid.uuid4())
    user_payload = {
        "id": new_user["id"],
        "username": new_user["username"],
        "name": new_user["name"],
        "role": new_user["role"],
        "email": new_user["email"]
    }
    active_tokens[token] = user_payload

    log_request(str(uuid.uuid4())[:8], f"USER REGISTER: {username} ({role})", "Gateway", "success", reason="auth_register")

    return jsonify({
        "message": "Registration successful!",
        "token": token,
        "user": user_payload
    }), 201

@app.route('/api/me', methods=['GET'])
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": user})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    if token and token in active_tokens:
        del active_tokens[token]
    return jsonify({"message": "Logged out successfully"})

@app.route('/api/users', methods=['GET'])
def api_users():
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Admin privilege required"}), 403
    sanitized = [
        {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"], "email": u["email"]}
        for u in users
    ]
    return jsonify(sanitized)

# ---------------- BOOKS ENDPOINTS (DISTRIBUTED MERGE & BALANCING) ---------------- #

@app.route('/books', methods=['GET'])
def get_books():
    """
    Distributed book retrieval: Queries all healthy servers and merges results.
    If one server is offline, returns books from available servers with a partial flag.
    """
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()
    all_books = []
    offline_servers = []
    successful_servers = []

    query = request.args.get("query", "")
    genre = request.args.get("genre", "")
    availability = request.args.get("availability", "")

    params = {}
    if query: params["query"] = query
    if genre: params["genre"] = genre
    if availability: params["availability"] = availability

    for s_key, server in backend_servers.items():
        if server.get("simulated_offline", False) or server["status"] == "OFFLINE":
            offline_servers.append(server["name"])
            continue

        try:
            resp = requests.get(f"{server['url']}/books", params=params, timeout=2.5)
            if resp.status_code == 200:
                b_list = resp.json()
                all_books.extend(b_list)
                successful_servers.append(server["name"])
            else:
                offline_servers.append(server["name"])
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException):
            offline_servers.append(server["name"])
            server["status"] = "OFFLINE"
            server["failure_count"] += 1

    dur_ms = round((time.time() - start_t) * 1000.0, 2)
    status_str = "success" if successful_servers else "error"
    log_request(
        request_id,
        "GET /books (Distributed Merge)",
        ", ".join(successful_servers) or "None",
        status_str,
        reason="distributed_merge",
        duration_ms=dur_ms
    )

    response = jsonify(all_books)
    if offline_servers:
        response.headers["X-Partial-Results"] = "true"
        response.headers["X-Offline-Servers"] = ", ".join(offline_servers)
    return response

@app.route('/books/<book_id>', methods=['GET'])
def get_single_book(book_id):
    """Fetch single book by checking backend servers."""
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()

    for s_key, server in backend_servers.items():
        if server["status"] == "OFFLINE" or server.get("simulated_offline", False):
            continue
        try:
            resp = requests.get(f"{server['url']}/books/{book_id}", timeout=2.0)
            if resp.status_code == 200:
                dur_ms = round((time.time() - start_t) * 1000.0, 2)
                log_request(request_id, f"GET /books/{book_id}", server["name"], "success", duration_ms=dur_ms)
                return jsonify(resp.json())
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException):
            pass

    return jsonify({"error": f"Book '{book_id}' not found on any active server"}), 404

@app.route('/add_book', methods=['POST'])
@app.route('/books', methods=['POST'])
def add_book():
    """
    Dynamic Load Balancing & Partitioning:
    Routes book creation to eligible server based on genre partition and server health.
    If primary partition server is OFFLINE, automatically fails over to healthy server!
    """
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()
    data = request.get_json() or {}

    title = data.get("title", "").strip()
    genre = data.get("genre", "Fiction").strip()
    author = data.get("author", "Unknown Author").strip()

    if not title:
        return jsonify({"error": "Book title is required"}), 400

    # Select best server using dynamic load balancing
    best_server, reason, score = select_best_server(genre=genre, read_only=False)

    if not best_server:
        log_request(request_id, f'ADD "{title}"', "Gateway", "error", reason="all_servers_offline")
        return jsonify({"error": "All backend servers are currently OFFLINE. Cannot add book."}), 503

    # Generate globally unique book ID
    prefix = "bk-a" if best_server["id"] == "server_a" else "bk-b"
    new_id = generate_id(prefix=prefix)

    payload = {
        "id": new_id,
        "title": title,
        "genre": genre,
        "author": author
    }

    try:
        r = requests.post(f"{best_server['url']}/add_book", json=payload, timeout=2.5)
        dur_ms = round((time.time() - start_t) * 1000.0, 2)

        if r.status_code in (200, 201):
            log_request(
                request_id,
                f'ADD "{title}" ({genre})',
                best_server["name"],
                "success",
                routing_score=score,
                reason=reason,
                duration_ms=dur_ms
            )
            resp_data = r.json()
            resp_data["routed_server"] = best_server["name"]
            resp_data["routing_reason"] = reason
            return jsonify(resp_data), 201
        else:
            log_request(request_id, f'ADD "{title}"', best_server["name"], "error", routing_score=score, reason=reason)
            return jsonify({"error": "Backend returned error"}), r.status_code

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException) as e:
        # Mark server offline and attempt instant failover to other server
        best_server["status"] = "OFFLINE"
        best_server["failure_count"] += 1
        print(f"[Gateway] Error routing to {best_server['name']}: {e}. Attempting failover...")

        alt_server = next((s for s in backend_servers.values() if s["id"] != best_server["id"] and s["status"] != "OFFLINE"), None)
        if alt_server:
            try:
                r2 = requests.post(f"{alt_server['url']}/add_book", json=payload, timeout=2.5)
                dur_ms = round((time.time() - start_t) * 1000.0, 2)
                if r2.status_code in (200, 201):
                    log_request(
                        request_id,
                        f'ADD "{title}" (FAILOVER -> {alt_server["name"]})',
                        alt_server["name"],
                        "success",
                        reason=f"failover_from_{best_server['id']}",
                        duration_ms=dur_ms
                    )
                    resp_data = r2.json()
                    resp_data["routed_server"] = alt_server["name"]
                    resp_data["routing_reason"] = "automatic_failover"
                    return jsonify(resp_data), 201
            except Exception:
                pass

        log_request(request_id, f'ADD "{title}"', best_server["name"], "error", reason="server_failed")
        return jsonify({"error": "Selected server failed and no failover server available"}), 500

@app.route('/books/<book_id>', methods=['PUT'])
def update_book(book_id):
    """Update book details (Admin only)."""
    user = get_current_user()
    if user and user.get("role") != "admin":
        return jsonify({"error": "Only administrators can edit books"}), 403

    request_id = str(uuid.uuid4())[:8]
    data = request.get_json() or {}

    for s_key, server in backend_servers.items():
        if server["status"] == "OFFLINE":
            continue
        try:
            r = requests.put(f"{server['url']}/books/{book_id}", json=data, timeout=2.5)
            if r.status_code == 200:
                log_request(request_id, f"UPDATE /books/{book_id}", server["name"], "success", reason="book_update")
                return jsonify(r.json())
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException):
            pass

    return jsonify({"error": "Book not found or servers unavailable"}), 404

@app.route('/books/<book_id>', methods=['DELETE'])
def delete_book(book_id):
    """Delete book (Admin only)."""
    user = get_current_user()
    if user and user.get("role") != "admin":
        return jsonify({"error": "Only administrators can delete books"}), 403

    request_id = str(uuid.uuid4())[:8]

    for s_key, server in backend_servers.items():
        if server["status"] == "OFFLINE":
            continue
        try:
            r = requests.delete(f"{server['url']}/books/{book_id}", timeout=2.5)
            if r.status_code == 200:
                log_request(request_id, f"DELETE /books/{book_id}", server["name"], "success", reason="book_delete")
                return jsonify(r.json())
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException):
            pass

    return jsonify({"error": "Book not found or servers unavailable"}), 404

# ---------------- ISSUE / RETURN FLOW ---------------- #

@app.route('/books/<book_id>/issue', methods=['POST'])
def issue_book_gateway(book_id):
    """
    Issue Book:
    1. Validates book availability across backend servers.
    2. Issues book to student.
    3. Records transaction in issue audit log.
    """
    request_id = str(uuid.uuid4())[:8]
    user = get_current_user()
    username = (user.get("username") if user else request.json.get("username", "student")) if request.json else "student"
    due_days = int(request.json.get("due_days", 14)) if request.json else 14

    for s_key, server in backend_servers.items():
        if server["status"] == "OFFLINE":
            continue
        try:
            resp = requests.post(
                f"{server['url']}/books/{book_id}/issue",
                json={"username": username, "due_days": due_days},
                timeout=2.5
            )
            if resp.status_code == 200:
                data = resp.json()
                b = data.get("book", {})

                # Record issue record in Gateway
                record = {
                    "id": generate_id(prefix="iss"),
                    "book_id": book_id,
                    "book_title": b.get("title", "Unknown"),
                    "username": username,
                    "issue_date": b.get("issue_date") or datetime.date.today().isoformat(),
                    "due_date": b.get("due_date") or (datetime.date.today() + datetime.timedelta(days=due_days)).isoformat(),
                    "return_date": None,
                    "fine_amount": 0.0,
                    "status": "Issued"
                }
                issue_records.insert(0, record)
                save_issues()

                log_request(request_id, f"ISSUE '{b.get('title')}' -> {username}", server["name"], "success", reason="book_issue")
                return jsonify({
                    "message": f"Book '{b.get('title')}' issued successfully to {username}",
                    "record": record,
                    "book": b
                })
            elif resp.status_code == 400:
                return jsonify(resp.json()), 400
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException):
            pass

    return jsonify({"error": "Book not found or server unavailable"}), 404

@app.route('/books/<book_id>/return', methods=['POST'])
def return_book_gateway(book_id):
    """
    Return Book:
    1. Returns book on backend server.
    2. Calculates overdue days & fines.
    3. Updates issue transaction record in Gateway.
    """
    request_id = str(uuid.uuid4())[:8]

    for s_key, server in backend_servers.items():
        if server["status"] == "OFFLINE":
            continue
        try:
            resp = requests.post(f"{server['url']}/books/{book_id}/return", timeout=2.5)
            if resp.status_code == 200:
                data = resp.json()
                b = data.get("book", {})
                overdue_days = data.get("overdue_days", 0)
                fine_amount = data.get("fine_amount", 0.0)

                # Update matching issue record
                for rec in issue_records:
                    if rec["book_id"] == book_id and rec["status"] == "Issued":
                        rec["status"] = "Returned"
                        rec["return_date"] = datetime.date.today().isoformat()
                        rec["fine_amount"] = fine_amount
                        break
                save_issues()

                log_request(
                    request_id,
                    f"RETURN '{b.get('title')}' (Fine: ₹{fine_amount})",
                    server["name"],
                    "success",
                    reason="book_return"
                )
                return jsonify({
                    "message": f"Book '{b.get('title')}' successfully returned",
                    "overdue_days": overdue_days,
                    "fine_amount": fine_amount,
                    "book": b
                })
            elif resp.status_code == 400:
                return jsonify(resp.json()), 400
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException):
            pass

    return jsonify({"error": "Book not found or server unavailable"}), 404

@app.route('/my_books', methods=['GET'])
def get_my_books():
    """Returns currently issued books for current student."""
    user = get_current_user()
    username = user["username"] if user else request.args.get("username", "student")

    my_issues = [r for r in issue_records if r.get("username") == username and r.get("status") == "Issued"]

    # Calculate real-time fines for active issues
    for iss in my_issues:
        days, fine = calculate_fine(iss.get("due_date"))
        iss["overdue_days"] = days
        iss["current_fine"] = fine

    return jsonify(my_issues)

@app.route('/issue_records', methods=['GET'])
def get_issue_records():
    """Returns all transaction history for librarian / admin."""
    return jsonify(issue_records)

# ---------------- DISTRIBUTED SEARCH ---------------- #

@app.route('/search', methods=['GET'])
def search():
    """
    Distributed Search:
    Searches across all healthy servers simultaneously and merges results.
    Handles partial failure gracefully.
    """
    query = request.args.get("query", "").strip()
    genre = request.args.get("genre", "").strip()
    availability = request.args.get("availability", "").strip()
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()

    results = []
    seen_ids = set()
    offline_servers = []

    params = {"query": query}
    if genre: params["genre"] = genre
    if availability: params["availability"] = availability

    for s_key, server in backend_servers.items():
        if server["status"] == "OFFLINE" or server.get("simulated_offline", False):
            offline_servers.append(server["name"])
            continue

        try:
            resp = requests.get(f"{server['url']}/search", params=params, timeout=2.5)
            if resp.status_code == 200:
                items = resp.json()
                for b in items:
                    if b["id"] not in seen_ids:
                        seen_ids.add(b["id"])
                        results.append(b)
            else:
                offline_servers.append(server["name"])
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException):
            offline_servers.append(server["name"])

    dur_ms = round((time.time() - start_t) * 1000.0, 2)
    log_request(
        request_id,
        f'SEARCH "{query}"',
        "Distributed (" + ", ".join([s["name"] for s in backend_servers.values() if s["name"] not in offline_servers]) + ")",
        "success",
        duration_ms=dur_ms
    )

    response = jsonify(results)
    if offline_servers:
        response.headers["X-Partial-Results"] = "true"
        response.headers["X-Offline-Servers"] = ", ".join(offline_servers)
    return response

# ---------------- SYSTEM STATUS & ANALYTICS ---------------- #

@app.route('/server_status', methods=['GET'])
def server_status():
    """Comprehensive real-time status of all backend servers and Gateway load balancer."""
    total_books = 0
    active_servers = 0
    server_data = {}

    for s_key, s in backend_servers.items():
        port = str(s["port"])
        is_active = s["status"] in ("HEALTHY", "RECOVERED") and not s.get("simulated_offline", False)
        if is_active:
            active_servers += 1
            total_books += s["books_count"]

        # Calculate routing score
        score = calculate_routing_score({
            "active_requests": s["active_requests"],
            "latency_ms": s["latency_ms"],
            "cpu_percent": s["cpu_percent"],
            "total_requests": s["total_requests"]
        })

        server_data[port] = {
            "id": s["id"],
            "name": s["name"],
            "port": s["port"],
            "books": s["books_count"],
            "requests": s["total_requests"],
            "active_requests": s["active_requests"],
            "latency_ms": s["latency_ms"],
            "cpu_percent": s["cpu_percent"],
            "status": "active" if is_active else "down",
            "detailed_status": s["status"] if not s.get("simulated_offline", False) else "OFFLINE",
            "routing_score": score,
            "last_request": s["last_successful_response"],
            "last_health_check": s["last_health_check"],
            "failure_detected_at": s["failure_detected_at"],
            "recovery_detected_at": s["recovery_detected_at"],
            "failure_count": s["failure_count"],
            "genres": s["genres"]
        }

    return jsonify({
        "total_servers": len(backend_servers),
        "active_servers": active_servers,
        "total_books": total_books,
        "total_requests": len(logs),
        "servers": server_data,
        "routing_policy": "Dynamic Weighted Scoring with Automatic Failover"
    })

@app.route('/analytics', methods=['GET'])
def get_analytics():
    """Complete analytics for library management and distributed system metrics."""
    # 1. Server request statistics
    req_a = backend_servers["server_a"]["total_requests"]
    req_b = backend_servers["server_b"]["total_requests"]
    total_srv_req = max(1, req_a + req_b)
    share_a = round((req_a / total_srv_req) * 100.0, 1)
    share_b = round((req_b / total_srv_req) * 100.0, 1)

    # 2. Gather all books from active servers for library metrics
    all_books = []
    for s in backend_servers.values():
        if s["status"] != "OFFLINE" and not s.get("simulated_offline", False):
            try:
                r = requests.get(f"{s['url']}/books", timeout=1.5)
                if r.status_code == 200:
                    all_books.extend(r.json())
            except Exception:
                pass

    total_books = len(all_books)
    issued_books = len([b for b in all_books if b.get("availability") == "Issued"])
    available_books = total_books - issued_books

    # Overdue count
    overdue_count = 0
    today = datetime.date.today()
    for b in all_books:
        if b.get("availability") == "Issued" and b.get("due_date"):
            try:
                due = datetime.datetime.strptime(b["due_date"], "%Y-%m-%d").date()
                if today > due:
                    overdue_count += 1
            except Exception:
                pass

    # Popular books (most issued)
    popular_books = sorted(all_books, key=lambda b: b.get("issue_count", 0), reverse=True)[:5]

    # Genre breakdown
    genre_counts = {}
    for b in all_books:
        g = b.get("genre", "Other")
        genre_counts[g] = genre_counts.get(g, 0) + 1

    return jsonify({
        "library_stats": {
            "total_books": total_books,
            "total_users": len(users),
            "issued_books": issued_books,
            "available_books": available_books,
            "overdue_books": overdue_count
        },
        "popular_books": [
            {"id": b["id"], "title": b["title"], "author": b.get("author"), "genre": b["genre"], "issue_count": b.get("issue_count", 0)}
            for b in popular_books
        ],
        "genre_distribution": genre_counts,
        "distributed_metrics": {
            "server_a": {
                "name": "Server A",
                "status": backend_servers["server_a"]["status"],
                "total_requests": req_a,
                "share_percent": share_a,
                "latency_ms": backend_servers["server_a"]["latency_ms"],
                "active_requests": backend_servers["server_a"]["active_requests"],
                "failures": backend_servers["server_a"]["failure_count"]
            },
            "server_b": {
                "name": "Server B",
                "status": backend_servers["server_b"]["status"],
                "total_requests": req_b,
                "share_percent": share_b,
                "latency_ms": backend_servers["server_b"]["latency_ms"],
                "active_requests": backend_servers["server_b"]["active_requests"],
                "failures": backend_servers["server_b"]["failure_count"]
            }
        }
    })

@app.route('/logs', methods=['GET'])
def get_logs():
    return jsonify(logs)

# ---------------- SIMULATION CONTROLS FOR LIVE PRESENTATION ---------------- #

@app.route('/api/simulate_failover', methods=['POST'])
def api_simulate_failover():
    """Toggle simulated offline status for a server to test failover."""
    data = request.json or {}
    server_target = data.get("server", "server_a")  # server_a or server_b
    if server_target in backend_servers:
        s = backend_servers[server_target]
        s["simulated_offline"] = not s.get("simulated_offline", False)
        if s["simulated_offline"]:
            s["status"] = "OFFLINE"
            s["failure_detected_at"] = datetime.datetime.now().isoformat()
            s["failure_count"] += 1
            msg = f"{s['name']} is now SIMULATED OFFLINE. Gateway will automatically failover!"
        else:
            s["status"] = "RECOVERED"
            s["recovery_detected_at"] = datetime.datetime.now().isoformat()
            msg = f"{s['name']} has RECOVERED. Added back to eligible pool!"

        log_request(str(uuid.uuid4())[:8], f"SIMULATION: {msg}", s["url"], "success", reason="simulation_toggle")
        return jsonify({"message": msg, "server": server_target, "is_offline": s["simulated_offline"]})

    return jsonify({"error": "Invalid server target"}), 400

@app.route('/api/simulate_load', methods=['POST'])
def api_simulate_load():
    """Simulate heavy load spike on Server A to demo dynamic load shifting to Server B."""
    data = request.json or {}
    server_target = data.get("server", "server_a")
    if server_target in backend_servers:
        s = backend_servers[server_target]
        try:
            requests.post(f"{s['url']}/simulate_load", json={"delay_sec": 0.25, "active_requests": 8}, timeout=2.0)
            s["active_requests"] += 8
            s["latency_ms"] += 250.0
            msg = f"Heavy load spike applied to {s['name']}. Routing score increased!"
            log_request(str(uuid.uuid4())[:8], f"SIMULATION: {msg}", s["url"], "success", reason="load_spike")
            return jsonify({"message": msg, "server": server_target})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Invalid server"}), 400

@app.route('/api/reset_simulation', methods=['POST'])
def api_reset_simulation():
    """Reset all simulated loads and outages."""
    for s_key, s in backend_servers.items():
        s["simulated_offline"] = False
        s["status"] = "HEALTHY"
        try:
            requests.post(f"{s['url']}/reset_simulation", timeout=1.5)
        except Exception:
            pass
    return jsonify({"message": "All simulations reset to normal operational state."})

# ---------------- STATIC FRONTEND PAGE ROUTES ---------------- #

@app.route('/')
def home():
    return send_from_directory(FRONTEND_DIR, "dashboard.html")

@app.route('/login')
def login_page():
    return send_from_directory(FRONTEND_DIR, "login.html")

@app.route('/signup')
def signup_page():
    return send_from_directory(FRONTEND_DIR, "signup.html")

@app.route('/dashboard')
def dashboard_page():
    return send_from_directory(FRONTEND_DIR, "dashboard.html")

@app.route('/logs_page')
def logs_page():
    return send_from_directory(FRONTEND_DIR, "logs.html")

@app.route('/servers')
def servers_page():
    return send_from_directory(FRONTEND_DIR, "servers.html")

@app.route('/style.css')
def serve_root_css():
    return send_from_directory(FRONTEND_DIR, "style.css", mimetype="text/css")

@app.route('/static/style.css')
def serve_static_css():
    return send_from_directory(FRONTEND_DIR, "style.css", mimetype="text/css")

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(FRONTEND_DIR, filename)

if __name__ == "__main__":
    load_data()
    print("[Gateway Load Balancer] Starting on port 5000...")
    print(f"[Gateway Load Balancer] Server A -> {SERVER_A_URL}")
    print(f"[Gateway Load Balancer] Server B -> {SERVER_B_URL}")
    app.run(port=5000, debug=False)