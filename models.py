import datetime
import uuid
import hashlib
import json
import os

def generate_id(prefix="bk"):
    """Generate a globally unique ID with optional prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

def hash_password(password: str) -> str:
    """Hash password with sha256."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    """Verify password matches hash."""
    return hash_password(password) == hashed

# ==================================================
# PRIMARY + SECONDARY/REPLICA TOPOLOGY CONFIGURATION
# ==================================================
# Logical Partitioning:
# Server A Primary: Fiction, Fantasy, Adventure
# Server A Replica: Science, Technology, Math
# Server B Primary: Science, Technology, Math
# Server B Replica: Fiction, Fantasy, Adventure

GENRE_TOPOLOGY = {
    "Fiction": {"primary": "server_a", "replica": "server_b"},
    "Fantasy": {"primary": "server_a", "replica": "server_b"},
    "Adventure": {"primary": "server_a", "replica": "server_b"},
    "Science": {"primary": "server_b", "replica": "server_a"},
    "Technology": {"primary": "server_b", "replica": "server_a"},
    "Math": {"primary": "server_b", "replica": "server_a"},
}

SERVER_GENRES = {
    "server_a": {
        "id": "server_a",
        "name": "Server A",
        "port": 5001,
        "primary": ["Fiction", "Fantasy", "Adventure"],
        "replica": ["Science", "Technology", "Math"]
    },
    "server_b": {
        "id": "server_b",
        "name": "Server B",
        "port": 5002,
        "primary": ["Science", "Technology", "Math"],
        "replica": ["Fiction", "Fantasy", "Adventure"]
    }
}

def normalize_genre(genre: str) -> str:
    """Normalize genre capitalization to match topology."""
    if not genre:
        return "Fiction"
    g_lower = genre.strip().lower()
    for g in GENRE_TOPOLOGY:
        if g.lower() == g_lower:
            return g
    # Handle synonyms or tech variations
    if "tech" in g_lower:
        return "Technology"
    return genre.strip().title()

def get_genre_topology(genre: str):
    """Retrieve primary and replica server IDs for a genre."""
    norm = normalize_genre(genre)
    return GENRE_TOPOLOGY.get(norm, {"primary": "server_a", "replica": "server_b"})

def get_primary_server(genre: str) -> str:
    """Get the primary server ID for a given genre."""
    return get_genre_topology(genre)["primary"]

def get_replica_server(genre: str) -> str:
    """Get the secondary/replica server ID for a given genre."""
    return get_genre_topology(genre)["replica"]

def get_server_genres(server_id: str):
    """Return primary and replica genre lists for a server."""
    return SERVER_GENRES.get(server_id, {
        "primary": [],
        "replica": []
    })

def create_book(title, author, genre, server, book_id=None, availability="Available", issued_to=None, issue_date=None, due_date=None, issue_count=0, data_role="PRIMARY"):
    """Standardized book model dictionary with explicit data_role distinction."""
    norm_genre = normalize_genre(genre)
    return {
        "id": book_id or generate_id(prefix="bk-a" if "A" in str(server) else "bk-b"),
        "title": title.strip(),
        "author": (author or "Unknown Author").strip(),
        "genre": norm_genre,
        "server": server,
        "availability": availability,
        "issued_to": issued_to,
        "issue_date": issue_date,
        "due_date": due_date,
        "issue_count": issue_count,
        "data_role": data_role  # PRIMARY or REPLICA
    }

def calculate_fine(due_date_str, daily_fine_rate=10.0):
    """
    Calculate overdue fine.
    due_date_str: 'YYYY-MM-DD'
    Returns: (overdue_days, fine_amount)
    """
    if not due_date_str:
        return 0, 0.0
    try:
        due_date = datetime.datetime.strptime(due_date_str, "%Y-%m-%d").date()
        today = datetime.date.today()
        if today > due_date:
            days = (today - due_date).days
            return days, round(days * daily_fine_rate, 2)
    except Exception:
        pass
    return 0, 0.0

def calculate_routing_score(metrics):
    """
    Calculate routing score for dynamic load balancing.
    Lower score is preferred (least loaded / lowest latency).
    Score formula:
      - active_requests weight: 40% (high impact on server responsiveness)
      - latency_ms weight: 35% (response time metric)
      - cpu_percent weight: 15% (system utilization)
      - total_requests weight: 10% (even request distribution)
    """
    active = metrics.get("active_requests", 0)
    latency = metrics.get("latency_ms", 0.0)
    cpu = metrics.get("cpu_percent", 0.0)
    total = metrics.get("total_requests", 0)
    
    # Normalize components
    score = (active * 25.0) + (latency * 1.5) + (cpu * 0.5) + ((total % 100) * 0.1)
    return round(score, 2)
