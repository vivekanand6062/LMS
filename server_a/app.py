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
from models import generate_id, calculate_fine

try:
    import psutil
except ImportError:
    psutil = None

app = Flask(__name__)
CORS(app)

SERVER_NAME = "Server A"
SERVER_PORT = 5001
DATA_FILE = os.path.join(os.path.dirname(__file__), "server_a_books.json")

# Default book list with globally unique IDs and complete fields
DEFAULT_BOOKS = [
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
        "issue_count": 12
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
        "issue_count": 25
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
        "issue_count": 19
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
        "issue_count": 8
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
        "issue_count": 14
    }
]

# In-memory array of books
books = []

def load_books():
    global books
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                books = json.load(f)
                return
        except Exception as e:
            print(f"[Server A] Error loading books from {DATA_FILE}: {e}")
    books = [dict(b) for b in DEFAULT_BOOKS]
    save_books()

def save_books():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(books, f, indent=2)
    except Exception as e:
        print(f"[Server A] Error saving books to {DATA_FILE}: {e}")

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
        # Exponential moving average for latency
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

    return jsonify({
        "server": "server-a",
        "name": SERVER_NAME,
        "port": SERVER_PORT,
        "status": "healthy",
        "active_requests": effective_active,
        "total_requests": request_count,
        "latency_ms": round(effective_latency, 2),
        "cpu_percent": round(cpu, 1),
        "books_count": len(books),
        "last_request": last_request_time,
        "uptime_seconds": int(time.time() - start_time)
    })

@app.route('/status', methods=['GET'])
def status():
    """Backwards-compatible status endpoint with enhanced metrics."""
    cpu = 5.0
    if psutil:
        try:
            cpu = psutil.cpu_percent(interval=None)
        except Exception:
            pass
    effective_active = active_requests + simulated_active_load
    effective_latency = avg_latency_ms + (simulated_delay_sec * 1000.0)

    return jsonify({
        "books": len(books),
        "requests": request_count,
        "last_request": last_request_time,
        "status": "active",
        "active_requests": effective_active,
        "latency_ms": round(effective_latency, 2),
        "cpu_percent": round(cpu, 1),
        "server": SERVER_NAME
    })

# ---------------- BOOKS CRUD ---------------- #

@app.route('/books', methods=['GET'])
def get_books():
    query = request.args.get('query', '').strip().lower()
    genre = request.args.get('genre', '').strip()
    availability = request.args.get('availability', '').strip()

    filtered = books
    if query:
        filtered = [
            b for b in filtered
            if query in b['title'].lower()
            or query in b.get('author', '').lower()
            or query in b['genre'].lower()
        ]
    if genre and genre != "All":
        filtered = [b for b in filtered if b['genre'].lower() == genre.lower()]
    if availability and availability != "All":
        filtered = [b for b in filtered if b['availability'].lower() == availability.lower()]

    return jsonify(filtered)

@app.route('/books/<book_id>', methods=['GET'])
def get_book(book_id):
    for b in books:
        if str(b['id']) == str(book_id):
            return jsonify(b)
    return jsonify({"error": "Book not found"}), 404

@app.route('/add_book', methods=['POST'])
@app.route('/books', methods=['POST'])
def add_book():
    data = request.json or {}
    title = data.get('title', '').strip()
    genre = data.get('genre', '').strip()
    author = data.get('author', 'Unknown Author').strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    new_book = {
        "id": data.get("id") or generate_id(prefix="bk-a"),
        "title": title,
        "author": author or "Unknown Author",
        "genre": genre or "Fiction",
        "server": SERVER_NAME,
        "availability": "Available",
        "issued_to": None,
        "issue_date": None,
        "due_date": None,
        "issue_count": 0
    }

    books.append(new_book)
    save_books()
    return jsonify(new_book), 201

@app.route('/books/<book_id>', methods=['PUT'])
def update_book(book_id):
    data = request.json or {}
    for b in books:
        if str(b['id']) == str(book_id):
            if 'title' in data:
                b['title'] = data['title'].strip()
            if 'author' in data:
                b['author'] = data['author'].strip()
            if 'genre' in data:
                b['genre'] = data['genre'].strip()
            save_books()
            return jsonify(b)
    return jsonify({"error": "Book not found"}), 404

@app.route('/books/<book_id>', methods=['DELETE'])
def delete_book(book_id):
    global books
    initial_len = len(books)
    books = [b for b in books if str(b['id']) != str(book_id)]
    if len(books) < initial_len:
        save_books()
        return jsonify({"message": f"Book {book_id} deleted successfully"})
    return jsonify({"error": "Book not found"}), 404

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