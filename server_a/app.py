import os
import sys
import time
import datetime
import json
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS

# Add root directory to sys.path to access models
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models import (
    generate_id, calculate_fine, normalize_genre,
    GENRE_TOPOLOGY, SERVER_GENRES, get_server_genres
)

try:
    import psutil
except ImportError:
    psutil = None

app = Flask(__name__)
CORS(app)

SERVER_ID = "server_a"
SERVER_NAME = "Server A"
SERVER_PORT = 5001
DATA_FILE = os.path.join(os.path.dirname(__file__), "server_a_books.json")

PRIMARY_GENRES = ["Fiction", "Fantasy", "Adventure"]
REPLICA_GENRES = ["Science", "Technology", "Math"]

# Default Primary Books (Server A Authoritative)
DEFAULT_PRIMARY_BOOKS = [
    {
        "id": "bk-a-01",
        "title": "The Hobbit",
        "author": "J.R.R. Tolkien",
        "genre": "Fantasy",
        "server": SERVER_NAME,
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 12,
        "data_role": "PRIMARY"
    },
    {
        "id": "bk-a-02",
        "title": "Harry Potter and the Sorcerer's Stone",
        "author": "J.K. Rowling",
        "genre": "Fantasy",
        "server": SERVER_NAME,
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 25,
        "data_role": "PRIMARY"
    },
    {
        "id": "bk-a-03",
        "title": "The Lord of the Rings",
        "author": "J.R.R. Tolkien",
        "genre": "Fantasy",
        "server": SERVER_NAME,
        "availability": "Issued",
        "issued_to": "student",
        "issue_date": (datetime.date.today() - datetime.timedelta(days=5)).isoformat(),
        "due_date": (datetime.date.today() + datetime.timedelta(days=9)).isoformat(),
        "issue_count": 19,
        "data_role": "PRIMARY"
    },
    {
        "id": "bk-a-04",
        "title": "Treasure Island",
        "author": "Robert Louis Stevenson",
        "genre": "Adventure",
        "server": SERVER_NAME,
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 8,
        "data_role": "PRIMARY"
    },
    {
        "id": "bk-a-05",
        "title": "The Great Gatsby",
        "author": "F. Scott Fitzgerald",
        "genre": "Fiction",
        "server": SERVER_NAME,
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 14,
        "data_role": "PRIMARY"
    }
]

# Default Replica Books (Synchronized from Server B)
DEFAULT_REPLICA_BOOKS = [
    {
        "id": "bk-b-01",
        "title": "Introduction to Algorithms",
        "author": "Thomas H. Cormen",
        "genre": "Science",
        "server": "Server B",
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 18,
        "data_role": "REPLICA"
    },
    {
        "id": "bk-b-02",
        "title": "Calculus: Early Transcendentals",
        "author": "James Stewart",
        "genre": "Math",
        "server": "Server B",
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 10,
        "data_role": "REPLICA"
    },
    {
        "id": "bk-b-03",
        "title": "Physics for Engineers",
        "author": "Raymond A. Serway",
        "genre": "Science",
        "server": "Server B",
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 15,
        "data_role": "REPLICA"
    },
    {
        "id": "bk-b-04",
        "title": "Data Structures & Algorithms in Python",
        "author": "Michael T. Goodrich",
        "genre": "Technology",
        "server": "Server B",
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 22,
        "data_role": "REPLICA"
    },
    {
        "id": "bk-b-05",
        "title": "Clean Code",
        "author": "Robert C. Martin",
        "genre": "Technology",
        "server": "Server B",
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 30,
        "data_role": "REPLICA"
    }
]

# In-memory array of books (both Primary and Replica data)
books = []

def load_books():
    global books
    loaded = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception as e:
            print(f"[{SERVER_NAME}] Error loading books from {DATA_FILE}: {e}")
            loaded = []

    # Map existing books by ID to preserve state
    book_map = {}
    for b in loaded:
        # Normalize data_role
        if "data_role" not in b:
            g = normalize_genre(b.get("genre", ""))
            b["data_role"] = "PRIMARY" if g in PRIMARY_GENRES else "REPLICA"
        book_map[b["id"]] = b

    # Ensure all default primary books are present
    for pb in DEFAULT_PRIMARY_BOOKS:
        if pb["id"] not in book_map:
            book_map[pb["id"]] = dict(pb)

    # Ensure all default replica books are present
    for rb in DEFAULT_REPLICA_BOOKS:
        if rb["id"] not in book_map:
            book_map[rb["id"]] = dict(rb)

    books = list(book_map.values())
    save_books()

def save_books():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(books, f, indent=2)
    except Exception as e:
        print(f"[{SERVER_NAME}] Error saving books to {DATA_FILE}: {e}")

# Server Metrics tracking
start_time = time.time()
request_count = 0
active_requests = 0
active_lock = threading.Lock()
avg_latency_ms = 12.0
last_request_time = None

# Simulation flags for presentation demo
simulated_delay_sec = 0.0
simulated_active_load = 0

def update_time():
    global last_request_time
    last_request_time = datetime.datetime.now().strftime("%H:%M:%S")

@app.before_request
def before_request():
    global active_requests, request_count
    with active_lock:
        active_requests += 1
        request_count += 1
    request.start_time = time.time()
    update_time()
    if simulated_delay_sec > 0:
        time.sleep(simulated_delay_sec)

@app.after_request
def after_request(response):
    global active_requests, avg_latency_ms
    with active_lock:
        active_requests = max(0, active_requests - 1)
    if hasattr(request, 'start_time'):
        duration_ms = (time.time() - request.start_time) * 1000.0
        avg_latency_ms = (0.8 * avg_latency_ms) + (0.2 * duration_ms)
    return response

# ---------------- HEALTH & METRICS ---------------- #

@app.route('/health', methods=['GET'])
def health():
    cpu = 0.0
    if psutil:
        try:
            cpu = psutil.cpu_percent(interval=None)
        except Exception:
            cpu = 5.0
    else:
        cpu = 5.0

    effective_active = active_requests + simulated_active_load
    effective_latency = avg_latency_ms + (simulated_delay_sec * 1000.0)

    primary_count = len([b for b in books if b.get("data_role") == "PRIMARY"])
    replica_count = len([b for b in books if b.get("data_role") == "REPLICA"])

    return jsonify({
        "server": "server-a",
        "server_id": SERVER_ID,
        "name": SERVER_NAME,
        "port": SERVER_PORT,
        "status": "healthy",
        "active_requests": effective_active,
        "total_requests": request_count,
        "latency_ms": round(effective_latency, 2),
        "cpu_percent": round(cpu, 1),
        "books_count": len(books),
        "primary_books_count": primary_count,
        "replica_books_count": replica_count,
        "primary_genres": PRIMARY_GENRES,
        "replica_genres": REPLICA_GENRES,
        "last_request": last_request_time,
        "uptime_seconds": int(time.time() - start_time)
    })

@app.route('/status', methods=['GET'])
def status():
    """Backwards-compatible status endpoint with enhanced primary/replica metrics."""
    cpu = 5.0
    if psutil:
        try:
            cpu = psutil.cpu_percent(interval=None)
        except Exception:
            pass
    effective_active = active_requests + simulated_active_load
    effective_latency = avg_latency_ms + (simulated_delay_sec * 1000.0)

    primary_count = len([b for b in books if b.get("data_role") == "PRIMARY"])
    replica_count = len([b for b in books if b.get("data_role") == "REPLICA"])

    return jsonify({
        "books": len(books),
        "primary_books_count": primary_count,
        "replica_books_count": replica_count,
        "primary_genres": PRIMARY_GENRES,
        "replica_genres": REPLICA_GENRES,
        "requests": request_count,
        "last_request": last_request_time,
        "status": "active",
        "active_requests": effective_active,
        "latency_ms": round(effective_latency, 2),
        "cpu_percent": round(cpu, 1),
        "server": SERVER_NAME,
        "server_id": SERVER_ID
    })

# ---------------- BOOKS CRUD ---------------- #

@app.route('/books', methods=['GET'])
def get_books():
    """
    Get books with optional query, genre, availability, and data_role filter.
    Can serve both PRIMARY and REPLICA records.
    """
    query = request.args.get('query', '').strip().lower()
    genre = request.args.get('genre', '').strip()
    availability = request.args.get('availability', '').strip()
    role = request.args.get('data_role', request.args.get('role', '')).strip().upper()

    filtered = books
    if query:
        filtered = [
            b for b in filtered
            if query in b['title'].lower()
            or query in b.get('author', '').lower()
            or query in b['genre'].lower()
        ]
    if genre and genre != "All":
        norm_req = normalize_genre(genre).lower()
        filtered = [b for b in filtered if normalize_genre(b['genre']).lower() == norm_req]
    if availability and availability != "All":
        filtered = [b for b in filtered if b['availability'].lower() == availability.lower()]
    if role in ("PRIMARY", "REPLICA"):
        filtered = [b for b in filtered if b.get("data_role") == role]

    return jsonify(filtered)

@app.route('/books/<book_id>', methods=['GET'])
def get_book(book_id):
    """Fetch single book by ID from either primary or replica catalog."""
    for b in books:
        if str(b['id']) == str(book_id):
            return jsonify(b)
    return jsonify({"error": "Book not found"}), 404

@app.route('/add_book', methods=['POST'])
@app.route('/books', methods=['POST'])
def add_book():
    """Authoritative book creation on Primary Server."""
    data = request.json or {}
    title = data.get('title', '').strip()
    genre = normalize_genre(data.get('genre', 'Fiction'))
    author = data.get('author', 'Unknown Author').strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    new_id = data.get("id") or generate_id(prefix="bk-a")
    # Verify no duplicate ID
    for existing in books:
        if str(existing['id']) == str(new_id):
            return jsonify({"error": f"Book with ID {new_id} already exists"}), 409

    new_book = {
        "id": new_id,
        "title": title,
        "author": author or "Unknown Author",
        "genre": genre,
        "server": SERVER_NAME,
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 0,
        "data_role": "PRIMARY"
    }

    books.append(new_book)
    save_books()
    return jsonify(new_book), 201

@app.route('/books/<book_id>', methods=['PUT'])
def update_book(book_id):
    """Authoritative book update on Primary Server."""
    data = request.json or {}
    for b in books:
        if str(b['id']) == str(book_id):
            if 'title' in data:
                b['title'] = data['title'].strip()
            if 'author' in data:
                b['author'] = data['author'].strip()
            if 'genre' in data:
                b['genre'] = normalize_genre(data['genre'])
            if 'availability' in data:
                b['availability'] = data['availability']
            if 'issued_to' in data:
                b['issued_to'] = data['issued_to']
            if 'due_date' in data:
                b['due_date'] = data['due_date']
            if 'issue_date' in data:
                b['issue_date'] = data['issue_date']
            save_books()
            return jsonify(b)
    return jsonify({"error": "Book not found"}), 404

@app.route('/books/<book_id>', methods=['DELETE'])
def delete_book(book_id):
    """Authoritative book deletion on Primary Server."""
    global books
    initial_len = len(books)
    books = [b for b in books if str(b['id']) != str(book_id)]
    if len(books) < initial_len:
        save_books()
        return jsonify({"message": f"Book {book_id} deleted successfully"})
    return jsonify({"error": "Book not found"}), 404

# ---------------- REPLICATION ENDPOINTS ---------------- #

@app.route('/api/replicate/upsert', methods=['POST'])
def replicate_upsert():
    """
    Replication sync endpoint:
    Inserts or updates a replicated record from the authoritative partner server.
    Ensures identical logical book data without duplicate IDs.
    """
    data = request.json or {}
    book_id = data.get("id")
    if not book_id:
        return jsonify({"error": "Book ID required for replication"}), 400

    replicated_book = {
        "id": book_id,
        "title": data.get("title", "").strip(),
        "author": data.get("author", "Unknown Author").strip(),
        "genre": normalize_genre(data.get("genre", "Fiction")),
        "server": data.get("server", "Server B"),
        "availability": data.get("availability", "Available"),
        "issued_to": data.get("issued_to"),
        "issue_date": data.get("issue_date"),
        "due_date": data.get("due_date"),
        "issue_count": data.get("issue_count", 0),
        "data_role": "REPLICA",
        "replicated_at": datetime.datetime.now().isoformat()
    }

    # Find and update existing book in place to avoid duplicate IDs
    found = False
    for i, b in enumerate(books):
        if str(b['id']) == str(book_id):
            books[i] = replicated_book
            found = True
            break

    if not found:
        books.append(replicated_book)

    save_books()
    return jsonify({
        "status": "success",
        "action": "updated" if found else "created",
        "message": f"Replicated book {book_id} on {SERVER_NAME}",
        "book": replicated_book
    }), 200

@app.route('/api/replicate/delete/<book_id>', methods=['DELETE'])
def replicate_delete(book_id):
    """Replication delete endpoint: Removes replica when deleted on primary."""
    global books
    initial_len = len(books)
    books = [b for b in books if str(b['id']) != str(book_id)]
    if len(books) < initial_len:
        save_books()
        return jsonify({"status": "success", "message": f"Replica book {book_id} deleted"})
    return jsonify({"status": "not_found", "message": f"Replica book {book_id} not found"}), 200

@app.route('/api/replicate/sync_all', methods=['POST'])
def replicate_sync_all():
    """Batch replication / reconciliation on server recovery."""
    payload = request.json or {}
    items = payload.get("books", [])
    count = 0
    for item in items:
        b_id = item.get("id")
        if not b_id:
            continue
        item_copy = dict(item)
        item_copy["data_role"] = "REPLICA"
        item_copy["replicated_at"] = datetime.datetime.now().isoformat()

        found = False
        for i, b in enumerate(books):
            if str(b["id"]) == str(b_id):
                books[i] = item_copy
                found = True
                break
        if not found:
            books.append(item_copy)
        count += 1

    save_books()
    return jsonify({
        "status": "success",
        "synced_count": count,
        "total_books": len(books)
    })

@app.route('/api/export_books', methods=['GET'])
def export_books():
    """Exports all books for cross-node reconciliation."""
    return jsonify({
        "server_id": SERVER_ID,
        "server_name": SERVER_NAME,
        "books": books,
        "primary_count": len([b for b in books if b.get("data_role") == "PRIMARY"]),
        "replica_count": len([b for b in books if b.get("data_role") == "REPLICA"])
    })

# ---------------- SEARCH ---------------- #

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('query', '').lower().strip()
    if not query:
        return jsonify(books)

    results = [
        b for b in books
        if query in b['title'].lower()
        or query in b.get('author', '').lower()
        or query in b['genre'].lower()
    ]
    return jsonify(results)

# ---------------- ISSUE / RETURN ---------------- #

@app.route('/books/<book_id>/issue', methods=['POST'])
def issue_book(book_id):
    data = request.json or {}
    username = data.get('username', 'student')
    due_days = int(data.get('due_days', 14))

    for b in books:
        if str(b['id']) == str(book_id):
            if b['availability'] != "Available":
                return jsonify({"error": f"Book is already issued to {b.get('issued_to')}"}), 400

            today = datetime.date.today()
            due_date = today + datetime.timedelta(days=due_days)

            b['availability'] = "Issued"
            b['issued_to'] = username
            b['issue_date'] = today.isoformat()
            b['due_date'] = due_date.isoformat()
            b['issue_count'] = b.get('issue_count', 0) + 1

            save_books()
            return jsonify({
                "message": f"Book '{b['title']}' successfully issued to {username}",
                "book": b
            })

    return jsonify({"error": "Book not found"}), 404

@app.route('/books/<book_id>/return', methods=['POST'])
def return_book(book_id):
    for b in books:
        if str(b['id']) == str(book_id):
            if b['availability'] == "Available":
                return jsonify({"error": "Book is already marked as available"}), 400

            due_date_str = b.get('due_date')
            overdue_days, fine_amount = calculate_fine(due_date_str, daily_fine_rate=10.0)

            user = b.get('issued_to')
            b['availability'] = "Available"
            b['issued_to'] = None
            b['issue_date'] = None
            b['due_date'] = None

            save_books()
            return jsonify({
                "message": f"Book '{b['title']}' returned successfully",
                "book": b,
                "overdue_days": overdue_days,
                "fine_amount": fine_amount,
                "returned_by": user
            })

    return jsonify({"error": "Book not found"}), 404

# ---------------- SIMULATION CONTROLS FOR DEMO ---------------- #

@app.route('/simulate_load', methods=['POST'])
def simulate_load():
    global simulated_delay_sec, simulated_active_load
    data = request.json or {}
    simulated_delay_sec = float(data.get('delay_sec', 0.2))
    simulated_active_load = int(data.get('active_requests', 6))
    return jsonify({
        "message": f"Simulated load active on {SERVER_NAME}",
        "simulated_delay_sec": simulated_delay_sec,
        "simulated_active_load": simulated_active_load
    })

@app.route('/reset_simulation', methods=['POST'])
def reset_simulation():
    global simulated_delay_sec, simulated_active_load
    simulated_delay_sec = 0.0
    simulated_active_load = 0
    return jsonify({"message": f"Simulation reset for {SERVER_NAME}"})

if __name__ == '__main__':
    load_books()
    print(f"[{SERVER_NAME}] Starting on port {SERVER_PORT} with {len(books)} books...")
    app.run(port=SERVER_PORT, debug=False)