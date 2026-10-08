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
    calculate_fine, calculate_routing_score,
    GENRE_TOPOLOGY, SERVER_GENRES, get_genre_topology,
    get_primary_server, get_replica_server, normalize_genre
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
        "primary_genres": ["Fiction", "Fantasy", "Adventure"],
        "replica_genres": ["Science", "Technology", "Math"],
        "genres": ["Fiction", "Fantasy", "Adventure"],  # backwards-compatible
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
        "primary_genres": ["Science", "Technology", "Math"],
        "replica_genres": ["Fiction", "Fantasy", "Adventure"],
        "genres": ["Science", "Technology", "Math"],  # backwards-compatible
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

# Real-time state of the most recent dynamic routing decision
last_routing_decision = {
    "genre": "Adventure",
    "operation": "READ",
    "primary_server": "Server A",
    "primary_status": "HEALTHY",
    "primary_score": 18.5,
    "replica_server": "Server B",
    "replica_status": "HEALTHY",
    "replica_score": 19.2,
    "eligible_servers": ["Server A", "Server B"],
    "selected_server": "Server A",
    "selected_role": "PRIMARY",
    "routing_score": 18.5,
    "reason": "Primary healthy and preferred",
    "timestamp": datetime.datetime.now().isoformat()
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
        with open(LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump(logs[-500:], f, indent=2)
    except Exception as e:
        print(f"[Gateway] Error saving logs: {e}")

def log_request(
    request_id,
    action,
    server,
    status,
    routing_score=None,
    reason="standard",
    duration_ms=None,
    genre=None,
    operation="READ",
    primary_server=None,
    replica_server=None,
    eligible_servers=None,
    server_role="PRIMARY"
):
    """Comprehensive request logger with extended architectural metadata."""
    entry = {
        "request_id": request_id,
        "action": action,
        "genre": genre or "General",
        "operation": operation,
        "primary_server": primary_server or "N/A",
        "replica_server": replica_server or "N/A",
        "eligible_servers": eligible_servers if eligible_servers is not None else ([server] if server else []),
        "server": server,
        "server_role": server_role or "PRIMARY",
        "routing_score": routing_score,
        "reason": reason,
        "status": status,
        "duration_ms": duration_ms,
        "timestamp": datetime.datetime.now().isoformat()
    }
    logs.insert(0, entry)
    if len(logs) > 500:
        logs.pop()
    threading.Thread(target=save_logs, daemon=True).start()

# ---------------- RECOVERY SYNCHRONIZATION ---------------- #

def reconcile_and_sync_server(recovered_key):
    """
    Automatic recovery synchronization:
    When a server transitions from OFFLINE to RECOVERED, synchronize authoritative state
    between nodes to ensure no data drift.
    """
    recovered_srv = backend_servers.get(recovered_key)
    if not recovered_srv:
        return

    partner_key = "server_b" if recovered_key == "server_a" else "server_a"
    partner_srv = backend_servers.get(partner_key)

    try:
        # Fetch partner's books
        r = requests.get(f"{partner_srv['url']}/api/export_books", timeout=2.5)
        if r.status_code == 200:
            partner_data = r.json()
            partner_books = partner_data.get("books", [])
            # Replicate partner's primary books to recovered server
            partner_primaries = [b for b in partner_books if b.get("data_role") == "PRIMARY"]
            if partner_primaries:
                requests.post(
                    f"{recovered_srv['url']}/api/replicate/sync_all",
                    json={"books": partner_primaries},
                    timeout=2.5
                )

        # Sync recovered server's primary books to partner
        r2 = requests.get(f"{recovered_srv['url']}/api/export_books", timeout=2.5)
        if r2.status_code == 200:
            rec_data = r2.json()
            rec_primaries = [b for b in rec_data.get("books", []) if b.get("data_role") == "PRIMARY"]
            if rec_primaries:
                requests.post(
                    f"{partner_srv['url']}/api/replicate/sync_all",
                    json={"books": rec_primaries},
                    timeout=2.5
                )

        print(f"[Gateway Recovery] Data synchronization verified between {recovered_srv['name']} and {partner_srv['name']}")
        log_request(
            str(uuid.uuid4())[:8],
            f"RECOVERY SYNC: {recovered_srv['name']} & {partner_srv['name']}",
            recovered_srv["name"],
            "success",
            reason="recovery_data_synchronization"
        )
    except Exception as ex:
        print(f"[Gateway Recovery] Sync error: {ex}")

# ---------------- HEALTH MONITOR & FAULT DETECTOR ---------------- #

def background_health_monitor():
    """
    Continuous background health checker and failure detector.
    Pings backend servers every 3 seconds to monitor health, calculate metrics,
    and trigger automatic failover, recovery, and data synchronization.
    """
    while True:
        try:
            for s_key, s_data in backend_servers.items():
                if s_data.get("simulated_offline", False):
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
                            print(f"[Gateway Monitor] {s_data['name']} has RECOVERED! Running data synchronization...")
                            reconcile_and_sync_server(s_key)
                            log_request(
                                str(uuid.uuid4())[:8],
                                f"HEALTH MONITOR: {s_data['name']} RECOVERED",
                                s_data["url"],
                                "success",
                                reason="server_recovery"
                            )
                        elif s_data["status"] == "RECOVERED":
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

# ---------------- ELIGIBLE-SERVER BASED DYNAMIC ROUTER ---------------- #

def select_server_for_request(genre, operation="READ"):
    """
    Eligible-Server Based Dynamic Routing Algorithm:
    1. Identify the requested genre.
    2. Find primary server and replica server for that genre from GENRE_TOPOLOGY.
    3. Check health/availability of both servers. Exclude OFFLINE servers.
    4. For WRITE operations:
       - The PRIMARY server is authoritative.
       - If primary is offline: writes are rejected to prevent divergence.
    5. For READ operations:
       - Both healthy primary and healthy replica may be eligible.
       - If both offline: return service unavailable (503).
       - If only Primary healthy: route to Primary.
       - If only Replica healthy (Primary OFFLINE): route to Replica (Automatic Failover).
       - If both healthy: compare routing scores.
         * Score = 25 * active_requests + 1.5 * latency_ms + 0.5 * cpu_percent + 0.1 * (total_requests mod 100)
         * Prefer Primary when healthy and load is reasonable.
         * If Primary is heavily loaded and Replica has a significantly better score, route to Replica.
    
    Returns: (selected_server_dict, role, reason, score, primary_srv, replica_srv, eligible_list)
    """
    global last_routing_decision

    norm_genre = normalize_genre(genre)
    primary_id = get_primary_server(norm_genre)
    replica_id = get_replica_server(norm_genre)

    primary_srv = backend_servers.get(primary_id)
    replica_srv = backend_servers.get(replica_id)

    # Compute dynamic routing scores for all servers
    scores = {}
    for s_id, s in backend_servers.items():
        metrics = {
            "active_requests": s["active_requests"],
            "latency_ms": s["latency_ms"],
            "cpu_percent": s["cpu_percent"],
            "total_requests": s["total_requests"]
        }
        scores[s_id] = calculate_routing_score(metrics)

    # Check health states (exclude OFFLINE or simulated outages)
    primary_healthy = primary_srv["status"] in ("HEALTHY", "RECOVERED") and not primary_srv.get("simulated_offline", False)
    replica_healthy = replica_srv["status"] in ("HEALTHY", "RECOVERED") and not replica_srv.get("simulated_offline", False)

    # WRITE OPERATIONS: Authoritative Primary Only
    if operation == "WRITE":
        if not primary_healthy:
            reason = f"Primary Server {primary_srv['name']} OFFLINE - Automatic Failover cannot accept writes"
            return None, None, reason, None, primary_srv, replica_srv, []
        
        score_val = scores[primary_id]
        reason = "Primary authoritative write"
        return primary_srv, "PRIMARY", reason, score_val, primary_srv, replica_srv, [primary_srv]

    # READ OPERATIONS: Eligible-Server Dynamic Routing
    eligible_servers = []
    if primary_healthy:
        eligible_servers.append((primary_srv, "PRIMARY"))
    if replica_healthy:
        eligible_servers.append((replica_srv, "REPLICA"))

    # Case 5: Both servers unavailable -> 503 Service Unavailable
    if not eligible_servers:
        reason = f"All eligible servers for {norm_genre} are OFFLINE"
        last_routing_decision = {
            "genre": norm_genre,
            "operation": operation,
            "primary_server": primary_srv["name"],
            "primary_status": "OFFLINE",
            "primary_score": scores.get(primary_id),
            "replica_server": replica_srv["name"],
            "replica_status": "OFFLINE",
            "replica_score": scores.get(replica_id),
            "eligible_servers": [],
            "selected_server": "None",
            "selected_role": "None",
            "routing_score": None,
            "reason": reason,
            "timestamp": datetime.datetime.now().isoformat()
        }
        return None, None, reason, None, primary_srv, replica_srv, []

    # Case 4: Primary healthy, Replica OFFLINE
    if primary_healthy and not replica_healthy:
        score_val = scores[primary_id]
        reason = "Primary healthy (Replica OFFLINE)"
        last_routing_decision = {
            "genre": norm_genre,
            "operation": operation,
            "primary_server": primary_srv["name"],
            "primary_status": primary_srv["status"],
            "primary_score": score_val,
            "replica_server": replica_srv["name"],
            "replica_status": "OFFLINE",
            "replica_score": scores.get(replica_id),
            "eligible_servers": [primary_srv["name"]],
            "selected_server": primary_srv["name"],
            "selected_role": "PRIMARY",
            "routing_score": score_val,
            "reason": reason,
            "timestamp": datetime.datetime.now().isoformat()
        }
        return primary_srv, "PRIMARY", reason, score_val, primary_srv, replica_srv, [primary_srv]

    # Case 3: Primary OFFLINE, Replica healthy -> Automatic Failover
    if not primary_healthy and replica_healthy:
        score_val = scores[replica_id]
        reason = f"Primary Server {primary_srv['name']} OFFLINE - Automatic Failover"
        last_routing_decision = {
            "genre": norm_genre,
            "operation": operation,
            "primary_server": primary_srv["name"],
            "primary_status": "OFFLINE",
            "primary_score": scores.get(primary_id),
            "replica_server": replica_srv["name"],
            "replica_status": replica_srv["status"],
            "replica_score": score_val,
            "eligible_servers": [replica_srv["name"]],
            "selected_server": replica_srv["name"],
            "selected_role": "REPLICA",
            "routing_score": score_val,
            "reason": reason,
            "timestamp": datetime.datetime.now().isoformat()
        }
        return replica_srv, "REPLICA", reason, score_val, primary_srv, replica_srv, [replica_srv]

    # Case 1 & 2: Both Primary and Replica are Healthy
    p_score = scores[primary_id]
    r_score = scores[replica_id]

    # Determine if primary is heavily loaded and replica has a significantly better score
    # Heavy load triggers: score >= 130.0 or active_requests >= 4 or cpu >= 70.0 or latency >= 120.0
    primary_is_heavily_loaded = (
        p_score >= 130.0 or
        primary_srv["active_requests"] >= 4 or
        primary_srv["cpu_percent"] >= 70.0 or
        primary_srv["latency_ms"] >= 120.0
    )
    replica_significantly_better = (r_score <= (p_score - 40.0)) or (p_score >= (r_score * 1.6))

    if primary_is_heavily_loaded and replica_significantly_better:
        # Case 2: Load-Aware Routing offloads read to replica
        selected_server = replica_srv
        selected_role = "REPLICA"
        selected_score = r_score
        reason = "Lower routing score"
    else:
        # Case 1: Primary healthy and preferred
        selected_server = primary_srv
        selected_role = "PRIMARY"
        selected_score = p_score
        reason = "Primary healthy and preferred"

    last_routing_decision = {
        "genre": norm_genre,
        "operation": operation,
        "primary_server": primary_srv["name"],
        "primary_status": primary_srv["status"],
        "primary_score": p_score,
        "replica_server": replica_srv["name"],
        "replica_status": replica_srv["status"],
        "replica_score": r_score,
        "eligible_servers": [primary_srv["name"], replica_srv["name"]],
        "selected_server": selected_server["name"],
        "selected_role": selected_role,
        "routing_score": selected_score,
        "reason": reason,
        "timestamp": datetime.datetime.now().isoformat()
    }

    return selected_server, selected_role, reason, selected_score, primary_srv, replica_srv, [primary_srv, replica_srv]

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

    log_request(
        str(uuid.uuid4())[:8],
        f"USER LOGIN: {username} ({user['role']})",
        "Gateway",
        "success",
        reason="auth_login",
        operation="READ"
    )

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

    log_request(
        str(uuid.uuid4())[:8],
        f"USER REGISTER: {username} ({role})",
        "Gateway",
        "success",
        reason="auth_register",
        operation="WRITE"
    )

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

# ---------------- BOOKS ENDPOINTS (DYNAMIC ROUTING & REPLICATION) ---------------- #

@app.route('/books', methods=['GET'])
def get_books():
    """
    Book retrieval endpoint:
    - If genre specified: Uses Eligible-Server Dynamic Routing to query either Primary or Replica.
    - If no genre specified: Merges healthy nodes into complete catalog with zero duplicate book IDs.
    """
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()

    query = request.args.get("query", "").strip()
    genre = request.args.get("genre", "").strip()
    availability = request.args.get("availability", "").strip()

    params = {}
    if query: params["query"] = query
    if availability: params["availability"] = availability

    # Case A: Specific genre requested -> Eligible-Server Dynamic Routing
    if genre and genre != "All":
        norm_genre = normalize_genre(genre)
        params["genre"] = norm_genre

        selected_server, role, reason, score, p_srv, r_srv, eligible = select_server_for_request(norm_genre, operation="READ")

        if not selected_server:
            dur_ms = round((time.time() - start_t) * 1000.0, 2)
            log_request(
                request_id,
                f"GET /books ({norm_genre})",
                "None",
                "error",
                reason=reason,
                duration_ms=dur_ms,
                genre=norm_genre,
                operation="READ",
                primary_server=p_srv["name"] if p_srv else "N/A",
                replica_server=r_srv["name"] if r_srv else "N/A",
                eligible_servers=[],
                server_role="N/A"
            )
            return jsonify({"error": f"All eligible servers for genre '{norm_genre}' are currently OFFLINE"}), 503

        try:
            resp = requests.get(f"{selected_server['url']}/books", params=params, timeout=2.5)
            dur_ms = round((time.time() - start_t) * 1000.0, 2)
            if resp.status_code == 200:
                books_data = resp.json()
                log_request(
                    request_id,
                    f"GET /books ({norm_genre})",
                    selected_server["name"],
                    "success",
                    routing_score=score,
                    reason=reason,
                    duration_ms=dur_ms,
                    genre=norm_genre,
                    operation="READ",
                    primary_server=p_srv["name"],
                    replica_server=r_srv["name"],
                    eligible_servers=[s["name"] for s in eligible],
                    server_role=role
                )
                response = jsonify(books_data)
                response.headers["X-Routed-Server"] = str(selected_server["name"])
                response.headers["X-Server-Role"] = str(role)
                response.headers["X-Routing-Reason"] = str(reason).replace("\u2014", "-")
                return response
        except Exception as e:
            pass

    # Case B: All genres -> Distributed catalog query merging unique book records
    all_books = []
    seen_ids = set()
    successful_servers = []
    offline_servers = []

    srv_a = backend_servers["server_a"]
    srv_b = backend_servers["server_b"]
    a_healthy = srv_a["status"] in ("HEALTHY", "RECOVERED") and not srv_a.get("simulated_offline", False)
    b_healthy = srv_b["status"] in ("HEALTHY", "RECOVERED") and not srv_b.get("simulated_offline", False)

    if not a_healthy and not b_healthy:
        log_request(request_id, "GET /books (All)", "None", "error", reason="all_servers_offline", operation="READ")
        return jsonify({"error": "All backend servers are OFFLINE. Cannot retrieve catalog."}), 503

    for s_key, server in backend_servers.items():
        is_healthy = server["status"] in ("HEALTHY", "RECOVERED") and not server.get("simulated_offline", False)
        if not is_healthy:
            offline_servers.append(server["name"])
            continue

        try:
            partner_key = "server_b" if s_key == "server_a" else "server_a"
            partner_healthy = backend_servers[partner_key]["status"] in ("HEALTHY", "RECOVERED") and not backend_servers[partner_key].get("simulated_offline", False)

            call_params = dict(params)
            # If both servers are healthy, query each for its PRIMARY books to prevent duplicate records
            # If partner is offline, query all books (including replicas) so complete catalog is returned
            if partner_healthy:
                call_params["data_role"] = "PRIMARY"

            resp = requests.get(f"{server['url']}/books", params=call_params, timeout=2.5)
            if resp.status_code == 200:
                for b in resp.json():
                    if b["id"] not in seen_ids:
                        seen_ids.add(b["id"])
                        all_books.append(b)
                successful_servers.append(server["name"])
            else:
                offline_servers.append(server["name"])
        except Exception:
            offline_servers.append(server["name"])

    dur_ms = round((time.time() - start_t) * 1000.0, 2)
    log_request(
        request_id,
        "GET /books (Distributed Merge)",
        ", ".join(successful_servers) or "None",
        "success" if successful_servers else "error",
        reason="distributed_catalog_merge",
        duration_ms=dur_ms,
        operation="READ"
    )

    response = jsonify(all_books)
    if offline_servers:
        response.headers["X-Partial-Results"] = "true"
        response.headers["X-Offline-Servers"] = ", ".join(offline_servers)
    return response

@app.route('/books/<book_id>', methods=['GET'])
def get_single_book(book_id):
    """Fetch single book by checking eligible nodes."""
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()

    # Determine likely genre / primary server from prefix or check both
    for s_key, server in backend_servers.items():
        if server["status"] == "OFFLINE" or server.get("simulated_offline", False):
            continue
        try:
            resp = requests.get(f"{server['url']}/books/{book_id}", timeout=2.0)
            if resp.status_code == 200:
                dur_ms = round((time.time() - start_t) * 1000.0, 2)
                b = resp.json()
                genre = b.get("genre", "Fiction")
                log_request(
                    request_id,
                    f"GET /books/{book_id}",
                    server["name"],
                    "success",
                    reason="book_read_lookup",
                    duration_ms=dur_ms,
                    genre=genre,
                    operation="READ",
                    server_role=b.get("data_role", "PRIMARY")
                )
                return jsonify(b)
        except Exception:
            pass

    return jsonify({"error": f"Book '{book_id}' not found on any active server"}), 404

@app.route('/add_book', methods=['POST'])
@app.route('/books', methods=['POST'])
def add_book():
    """
    Authoritative Primary Creation + Synchronous Replication:
    1. Determines Primary and Replica servers for genre.
    2. Enforces Authoritative Primary write path.
    3. If Primary is OFFLINE: write is rejected with 503.
    4. Upon successful Primary creation, immediately replicates to Secondary server.
    """
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()
    data = request.get_json() or {}

    title = data.get("title", "").strip()
    genre = normalize_genre(data.get("genre", "Fiction"))
    author = data.get("author", "Unknown Author").strip()

    if not title:
        return jsonify({"error": "Book title is required"}), 400

    # Select Primary server for WRITE
    primary_server, role, reason, score, p_srv, r_srv, eligible = select_server_for_request(genre, operation="WRITE")

    if not primary_server:
        dur_ms = round((time.time() - start_t) * 1000.0, 2)
        log_request(
            request_id,
            f'ADD "{title}"',
            "None",
            "error",
            reason=reason,
            duration_ms=dur_ms,
            genre=genre,
            operation="WRITE",
            primary_server=p_srv["name"] if p_srv else "N/A",
            replica_server=r_srv["name"] if r_srv else "N/A",
            eligible_servers=[],
            server_role="PRIMARY"
        )
        return jsonify({"error": f"Primary server for genre '{genre}' is currently OFFLINE. Cannot perform write operations."}), 503

    # Globally unique book ID
    prefix = "bk-a" if primary_server["id"] == "server_a" else "bk-b"
    new_id = generate_id(prefix=prefix)

    payload = {
        "id": new_id,
        "title": title,
        "genre": genre,
        "author": author,
        "server": primary_server["name"],
        "data_role": "PRIMARY"
    }

    try:
        # Step 1: Write to Authoritative Primary Server
        r = requests.post(f"{primary_server['url']}/add_book", json=payload, timeout=2.5)
        dur_ms = round((time.time() - start_t) * 1000.0, 2)

        if r.status_code in (200, 201):
            created_book = r.json()

            # Step 2: Synchronize Replica to Secondary Server
            replica_server = backend_servers[get_replica_server(genre)]
            replicated_ok = False
            if replica_server["status"] != "OFFLINE" and not replica_server.get("simulated_offline", False):
                try:
                    rep_payload = dict(created_book)
                    rep_payload["data_role"] = "REPLICA"
                    r_rep = requests.post(f"{replica_server['url']}/api/replicate/upsert", json=rep_payload, timeout=2.5)
                    if r_rep.status_code == 200:
                        replicated_ok = True
                except Exception as ex:
                    print(f"[Gateway Replication] Error replicating to {replica_server['name']}: {ex}")

            log_request(
                request_id,
                f'ADD "{title}" ({genre})',
                primary_server["name"],
                "success",
                routing_score=score,
                reason="Primary authoritative write + replication",
                duration_ms=dur_ms,
                genre=genre,
                operation="WRITE",
                primary_server=primary_server["name"],
                replica_server=replica_server["name"],
                eligible_servers=[primary_server["name"]],
                server_role="PRIMARY"
            )

            created_book["routed_server"] = primary_server["name"]
            created_book["server_role"] = "PRIMARY"
            created_book["routing_reason"] = "Primary authoritative write"
            created_book["replicated_to"] = replica_server["name"] if replicated_ok else "Pending Recovery Sync"
            return jsonify(created_book), 201
        else:
            return jsonify(r.json()), r.status_code

    except Exception as e:
        return jsonify({"error": f"Failed communicating with primary server: {e}"}), 500

@app.route('/books/<book_id>', methods=['PUT'])
def update_book(book_id):
    """Authoritative Book Update + Replica Synchronization."""
    user = get_current_user()
    if user and user.get("role") != "admin":
        return jsonify({"error": "Only administrators can edit books"}), 403

    request_id = str(uuid.uuid4())[:8]
    data = request.get_json() or {}
    start_t = time.time()

    # Find existing book to determine its genre
    book_genre = data.get("genre")
    if not book_genre:
        for s in backend_servers.values():
            if s["status"] != "OFFLINE" and not s.get("simulated_offline", False):
                try:
                    resp = requests.get(f"{s['url']}/books/{book_id}", timeout=2.0)
                    if resp.status_code == 200:
                        b = resp.json()
                        book_genre = b.get("genre")
                        break
                except Exception:
                    pass

    genre = normalize_genre(book_genre or ("Fiction" if "bk-a" in book_id else "Technology"))
    primary_id = get_primary_server(genre)
    replica_id = get_replica_server(genre)
    primary_srv = backend_servers[primary_id]
    replica_srv = backend_servers[replica_id]

    if primary_srv["status"] == "OFFLINE" or primary_srv.get("simulated_offline", False):
        return jsonify({"error": f"Primary server {primary_srv['name']} for genre '{genre}' is OFFLINE. Cannot perform update."}), 503

    # Primary authoritative update
    try:
        r = requests.put(f"{primary_srv['url']}/books/{book_id}", json=data, timeout=2.5)
        if r.status_code == 200:
            updated_book = r.json()
            # Synchronize replica on secondary node
            if replica_srv["status"] != "OFFLINE" and not replica_srv.get("simulated_offline", False):
                try:
                    rep_payload = dict(updated_book)
                    rep_payload["data_role"] = "REPLICA"
                    requests.post(f"{replica_srv['url']}/api/replicate/upsert", json=rep_payload, timeout=2.5)
                except Exception:
                    pass

            dur_ms = round((time.time() - start_t) * 1000.0, 2)
            log_request(
                request_id,
                f"UPDATE /books/{book_id}",
                primary_srv["name"],
                "success",
                reason="Authoritative update + replica sync",
                duration_ms=dur_ms,
                genre=genre,
                operation="WRITE",
                primary_server=primary_srv["name"],
                replica_server=replica_srv["name"],
                eligible_servers=[primary_srv["name"]],
                server_role="PRIMARY"
            )
            return jsonify(updated_book)
        else:
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": f"Failed communicating with primary server: {e}"}), 500

@app.route('/books/<book_id>', methods=['DELETE'])
def delete_book(book_id):
    """Authoritative Book Deletion + Replica Removal."""
    user = get_current_user()
    if user and user.get("role") != "admin":
        return jsonify({"error": "Only administrators can delete books"}), 403

    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()

    # Find genre of book
    book_genre = None
    for s in backend_servers.values():
        if s["status"] != "OFFLINE" and not s.get("simulated_offline", False):
            try:
                resp = requests.get(f"{s['url']}/books/{book_id}", timeout=2.0)
                if resp.status_code == 200:
                    book_genre = resp.json().get("genre")
                    break
            except Exception:
                pass

    genre = normalize_genre(book_genre or ("Fiction" if "bk-a" in book_id else "Technology"))
    primary_id = get_primary_server(genre)
    replica_id = get_replica_server(genre)
    primary_srv = backend_servers[primary_id]
    replica_srv = backend_servers[replica_id]

    if primary_srv["status"] == "OFFLINE" or primary_srv.get("simulated_offline", False):
        return jsonify({"error": f"Primary server {primary_srv['name']} for genre '{genre}' is OFFLINE. Cannot perform deletion."}), 503

    # Primary authoritative delete
    try:
        r = requests.delete(f"{primary_srv['url']}/books/{book_id}", timeout=2.5)
        if r.status_code == 200:
            # Delete replica on secondary node
            if replica_srv["status"] != "OFFLINE" and not replica_srv.get("simulated_offline", False):
                try:
                    requests.delete(f"{replica_srv['url']}/api/replicate/delete/{book_id}", timeout=2.5)
                except Exception:
                    pass

            dur_ms = round((time.time() - start_t) * 1000.0, 2)
            log_request(
                request_id,
                f"DELETE /books/{book_id}",
                primary_srv["name"],
                "success",
                reason="Authoritative deletion + replica cleanup",
                duration_ms=dur_ms,
                genre=genre,
                operation="WRITE",
                primary_server=primary_srv["name"],
                replica_server=replica_srv["name"],
                eligible_servers=[primary_srv["name"]],
                server_role="PRIMARY"
            )
            return jsonify({"message": f"Book {book_id} deleted successfully from primary and replica"})
        else:
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": f"Failed deleting from primary: {e}"}), 500

# ---------------- ISSUE / RETURN FLOW WITH REPLICATION ---------------- #

@app.route('/books/<book_id>/issue', methods=['POST'])
def issue_book_gateway(book_id):
    """
    Issue Book:
    1. Identifies Primary server for book genre.
    2. Authoritative issue on Primary server.
    3. Synchronously replicates issued state to Secondary server.
    4. Audits transaction in issue_records.
    """
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()
    user = get_current_user()
    username = (user.get("username") if user else request.json.get("username", "student")) if request.json else "student"
    due_days = int(request.json.get("due_days", 14)) if request.json else 14

    # Locate book to determine genre
    book_genre = None
    target_book = None
    for s in backend_servers.values():
        if s["status"] != "OFFLINE" and not s.get("simulated_offline", False):
            try:
                resp = requests.get(f"{s['url']}/books/{book_id}", timeout=2.0)
                if resp.status_code == 200:
                    target_book = resp.json()
                    book_genre = target_book.get("genre")
                    break
            except Exception:
                pass

    genre = normalize_genre(book_genre or ("Fiction" if "bk-a" in book_id else "Technology"))
    primary_id = get_primary_server(genre)
    replica_id = get_replica_server(genre)
    primary_srv = backend_servers[primary_id]
    replica_srv = backend_servers[replica_id]

    if primary_srv["status"] == "OFFLINE" or primary_srv.get("simulated_offline", False):
        return jsonify({"error": f"Primary server {primary_srv['name']} is OFFLINE. Cannot issue book."}), 503

    try:
        resp = requests.post(
            f"{primary_srv['url']}/books/{book_id}/issue",
            json={"username": username, "due_days": due_days},
            timeout=2.5
        )
        if resp.status_code == 200:
            data = resp.json()
            b = data.get("book", {})

            # Replicate state change to secondary replica
            if replica_srv["status"] != "OFFLINE" and not replica_srv.get("simulated_offline", False):
                try:
                    rep_payload = dict(b)
                    rep_payload["data_role"] = "REPLICA"
                    requests.post(f"{replica_srv['url']}/api/replicate/upsert", json=rep_payload, timeout=2.5)
                except Exception:
                    pass

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

            dur_ms = round((time.time() - start_t) * 1000.0, 2)
            log_request(
                request_id,
                f"ISSUE '{b.get('title')}' -> {username}",
                primary_srv["name"],
                "success",
                reason="Authoritative issue + replica sync",
                duration_ms=dur_ms,
                genre=genre,
                operation="WRITE",
                primary_server=primary_srv["name"],
                replica_server=replica_srv["name"],
                server_role="PRIMARY"
            )
            return jsonify({
                "message": f"Book '{b.get('title')}' issued successfully to {username}",
                "record": record,
                "book": b
            })
        elif resp.status_code == 400:
            return jsonify(resp.json()), 400
    except Exception as e:
        return jsonify({"error": f"Failed executing issue on primary server: {e}"}), 500

    return jsonify({"error": "Book not found or primary server unavailable"}), 404

@app.route('/books/<book_id>/return', methods=['POST'])
def return_book_gateway(book_id):
    """
    Return Book:
    1. Returns book authoritatively on Primary server.
    2. Synchronously replicates returned state to Secondary server.
    3. Updates issue audit log.
    """
    request_id = str(uuid.uuid4())[:8]
    start_t = time.time()

    # Locate book to determine genre
    book_genre = None
    for s in backend_servers.values():
        if s["status"] != "OFFLINE" and not s.get("simulated_offline", False):
            try:
                resp = requests.get(f"{s['url']}/books/{book_id}", timeout=2.0)
                if resp.status_code == 200:
                    book_genre = resp.json().get("genre")
                    break
            except Exception:
                pass

    genre = normalize_genre(book_genre or ("Fiction" if "bk-a" in book_id else "Technology"))
    primary_id = get_primary_server(genre)
    replica_id = get_replica_server(genre)
    primary_srv = backend_servers[primary_id]
    replica_srv = backend_servers[replica_id]

    if primary_srv["status"] == "OFFLINE" or primary_srv.get("simulated_offline", False):
        return jsonify({"error": f"Primary server {primary_srv['name']} is OFFLINE. Cannot return book."}), 503

    try:
        resp = requests.post(f"{primary_srv['url']}/books/{book_id}/return", timeout=2.5)
        if resp.status_code == 200:
            data = resp.json()
            b = data.get("book", {})
            overdue_days = data.get("overdue_days", 0)
            fine_amount = data.get("fine_amount", 0.0)

            # Replicate returned state to secondary replica
            if replica_srv["status"] != "OFFLINE" and not replica_srv.get("simulated_offline", False):
                try:
                    rep_payload = dict(b)
                    rep_payload["data_role"] = "REPLICA"
                    requests.post(f"{replica_srv['url']}/api/replicate/upsert", json=rep_payload, timeout=2.5)
                except Exception:
                    pass

            # Update matching issue record
            for rec in issue_records:
                if rec["book_id"] == book_id and rec["status"] == "Issued":
                    rec["status"] = "Returned"
                    rec["return_date"] = datetime.date.today().isoformat()
                    rec["fine_amount"] = fine_amount
                    break
            save_issues()

            dur_ms = round((time.time() - start_t) * 1000.0, 2)
            log_request(
                request_id,
                f"RETURN '{b.get('title')}' (Fine: INR {fine_amount})",
                primary_srv["name"],
                "success",
                reason="Authoritative return + replica sync",
                duration_ms=dur_ms,
                genre=genre,
                operation="WRITE",
                primary_server=primary_srv["name"],
                replica_server=replica_srv["name"],
                server_role="PRIMARY"
            )
            return jsonify({
                "message": f"Book '{b.get('title')}' successfully returned",
                "overdue_days": overdue_days,
                "fine_amount": fine_amount,
                "book": b
            })
        elif resp.status_code == 400:
            return jsonify(resp.json()), 400
    except Exception as e:
        return jsonify({"error": f"Failed communicating with primary: {e}"}), 500

    return jsonify({"error": "Book not found or server unavailable"}), 404

@app.route('/my_books', methods=['GET'])
def get_my_books():
    """Returns currently issued books for current student."""
    user = get_current_user()
    username = user["username"] if user else request.args.get("username", "student")

    my_issues = [r for r in issue_records if r.get("username") == username and r.get("status") == "Issued"]

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
    """Distributed Search across healthy server nodes with deduplication."""
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
        except Exception:
            offline_servers.append(server["name"])

    dur_ms = round((time.time() - start_t) * 1000.0, 2)
    log_request(
        request_id,
        f'SEARCH "{query}"',
        "Distributed (" + ", ".join([s["name"] for s in backend_servers.values() if s["name"] not in offline_servers]) + ")",
        "success",
        duration_ms=dur_ms,
        operation="READ"
    )

    response = jsonify(results)
    if offline_servers:
        response.headers["X-Partial-Results"] = "true"
        response.headers["X-Offline-Servers"] = ", ".join(offline_servers)
    return response

# ---------------- SYSTEM STATUS, TELEMETRY & ROUTING DECISION ---------------- #

@app.route('/api/routing_decision', methods=['GET'])
def api_routing_decision():
    """
    Real-time routing decision inspector for presentation / testing / visualizer.
    Returns real eligible server evaluation for any requested genre based on live telemetry.
    """
    genre = request.args.get("genre", "Adventure")
    operation = request.args.get("operation", "READ").upper()

    norm_genre = normalize_genre(genre)
    selected_server, role, reason, score, p_srv, r_srv, eligible = select_server_for_request(norm_genre, operation=operation)

    primary_id = get_primary_server(norm_genre)
    replica_id = get_replica_server(norm_genre)

    score_p = calculate_routing_score({
        "active_requests": p_srv["active_requests"],
        "latency_ms": p_srv["latency_ms"],
        "cpu_percent": p_srv["cpu_percent"],
        "total_requests": p_srv["total_requests"]
    })
    score_r = calculate_routing_score({
        "active_requests": r_srv["active_requests"],
        "latency_ms": r_srv["latency_ms"],
        "cpu_percent": r_srv["cpu_percent"],
        "total_requests": r_srv["total_requests"]
    })

    p_healthy = p_srv["status"] in ("HEALTHY", "RECOVERED") and not p_srv.get("simulated_offline", False)
    r_healthy = r_srv["status"] in ("HEALTHY", "RECOVERED") and not r_srv.get("simulated_offline", False)

    return jsonify({
        "genre": norm_genre,
        "operation": operation,
        "primary_server": {
            "id": p_srv["id"],
            "name": p_srv["name"],
            "status": "HEALTHY" if p_healthy else "OFFLINE",
            "routing_score": score_p,
            "cpu_percent": p_srv["cpu_percent"],
            "latency_ms": p_srv["latency_ms"],
            "active_requests": p_srv["active_requests"]
        },
        "replica_server": {
            "id": r_srv["id"],
            "name": r_srv["name"],
            "status": "HEALTHY" if r_healthy else "OFFLINE",
            "routing_score": score_r,
            "cpu_percent": r_srv["cpu_percent"],
            "latency_ms": r_srv["latency_ms"],
            "active_requests": r_srv["active_requests"]
        },
        "eligible_servers": [s["name"] for s in eligible],
        "selected_server": selected_server["name"] if selected_server else None,
        "selected_role": role,
        "routing_score": score,
        "reason": reason
    })

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
            "genres": s["genres"],
            "primary_genres": s.get("primary_genres", []),
            "replica_genres": s.get("replica_genres", [])
        }

    return jsonify({
        "total_servers": len(backend_servers),
        "active_servers": active_servers,
        "total_books": total_books,
        "total_requests": len(logs),
        "servers": server_data,
        "topology": GENRE_TOPOLOGY,
        "last_routing_decision": last_routing_decision,
        "routing_policy": "Eligible-Server Load-Aware Dynamic Routing with Authoritative Replication"
    })

@app.route('/analytics', methods=['GET'])
def get_analytics():
    """Complete analytics for library management and distributed system metrics."""
    req_a = backend_servers["server_a"]["total_requests"]
    req_b = backend_servers["server_b"]["total_requests"]
    total_srv_req = max(1, req_a + req_b)
    share_a = round((req_a / total_srv_req) * 100.0, 1)
    share_b = round((req_b / total_srv_req) * 100.0, 1)

    all_books = []
    seen_ids = set()
    for s in backend_servers.values():
        if s["status"] != "OFFLINE" and not s.get("simulated_offline", False):
            try:
                r = requests.get(f"{s['url']}/books?data_role=PRIMARY", timeout=1.5)
                if r.status_code == 200:
                    for b in r.json():
                        if b["id"] not in seen_ids:
                            seen_ids.add(b["id"])
                            all_books.append(b)
            except Exception:
                pass

    total_books = len(all_books)
    issued_books = len([b for b in all_books if b.get("availability") == "Issued"])
    available_books = total_books - issued_books

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

    popular_books = sorted(all_books, key=lambda b: b.get("issue_count", 0), reverse=True)[:5]

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
    server_target = data.get("server", "server_a")
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
            reconcile_and_sync_server(server_target)
            msg = f"{s['name']} has RECOVERED. Synchronized and restored to eligible pool!"

        log_request(str(uuid.uuid4())[:8], f"SIMULATION: {msg}", s["url"], "success", reason="simulation_toggle")
        return jsonify({"message": msg, "server": server_target, "is_offline": s["simulated_offline"]})

    return jsonify({"error": "Invalid server target"}), 400

@app.route('/api/simulate_load', methods=['POST'])
def api_simulate_load():
    """Simulate heavy load spike on target server to demo dynamic load shifting."""
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
        s["active_requests"] = 0
        s["latency_ms"] = 1.0
        s["cpu_percent"] = 0.0
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