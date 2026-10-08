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

def create_book(title, author, genre, server, book_id=None, availability="Available", issued_to=None, issue_date=None, due_date=None, issue_count=0):
    """Standardized book model dictionary."""
    return {
        "id": book_id or generate_id(prefix="bk-a" if "A" in str(server) else "bk-b"),
        "title": title.strip(),
        "author": (author or "Unknown Author").strip(),
        "genre": genre.strip(),
        "server": server,
        "availability": availability,
        "issued_to": issued_to,
        "issue_date": issue_date,
        "due_date": due_date,
        "issue_count": issue_count
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
