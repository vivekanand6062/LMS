import requests
import json
import time
import datetime

GATEWAY_URL = "http://localhost:5000"

def run_tests():
    print("==================================================")
    print("STARTING DISTRIBUTED LMS INTEGRATION TESTS")
    print("==================================================")

    # Clean initial state
    requests.post(f"{GATEWAY_URL}/api/reset_simulation")
    time.sleep(1.0)

    # ---------------- TEST 1: Normal Routing ---------------- #
    print("\n[TEST 1] Testing Normal Routing (Genre Partitioning & Balancing)...")
    res_a = requests.post(f"{GATEWAY_URL}/add_book", json={
        "title": "Dune",
        "genre": "Fiction",
        "author": "Frank Herbert"
    })
    assert res_a.status_code == 201, f"Failed adding Fiction book: {res_a.text}"
    data_a = res_a.json()
    print(f" -> Fiction book '{data_a['title']}' added. Routed to: {data_a.get('routed_server')} (Reason: {data_a.get('routing_reason')})")
    assert "Server A" in data_a.get("routed_server", "")

    res_b = requests.post(f"{GATEWAY_URL}/add_book", json={
        "title": "Artificial Intelligence: A Modern Approach",
        "genre": "Technology",
        "author": "Stuart Russell"
    })
    assert res_b.status_code == 201, f"Failed adding Tech book: {res_b.text}"
    data_b = res_b.json()
    print(f" -> Tech book '{data_b['title']}' added. Routed to: {data_b.get('routed_server')} (Reason: {data_b.get('routing_reason')})")
    assert "Server B" in data_b.get("routed_server", "")
    print("  [OK] PASS: Test 1 Normal routing succeeded!")

    # ---------------- TEST 2: Uneven Load & Dynamic Routing Score ---------------- #
    print("\n[TEST 2] Testing Uneven Load & Dynamic Load Balancing...")
    # Spike load on Server A
    requests.post(f"{GATEWAY_URL}/api/simulate_load", json={"server": "server_a"})
    time.sleep(1.0)
    status_resp = requests.get(f"{GATEWAY_URL}/server_status").json()
    score_a = status_resp["servers"]["5001"]["routing_score"]
    score_b = status_resp["servers"]["5002"]["routing_score"]
    print(f" -> After load spike on Server A: Server A Score = {score_a}, Server B Score = {score_b}")
    assert score_a > score_b, f"Expected Server A score ({score_a}) to be higher than Server B ({score_b})"
    print("  [OK] PASS: Test 2 Load spike accurately tracked in dynamic routing scores!")
    # Reset simulation
    requests.post(f"{GATEWAY_URL}/api/reset_simulation")

    # ---------------- TEST 3: Server Failure & Automatic Failover ---------------- #
    print("\n[TEST 3] Testing Server Failure & Automatic Failover...")
    # Simulate Server A failure
    fail_res = requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_a"}).json()
    print(f" -> Injected failure: {fail_res['message']}")
    time.sleep(1.0)

    # Check Gateway status
    gw_status = requests.get(f"{GATEWAY_URL}/server_status").json()
    server_a_state = gw_status["servers"]["5001"]["detailed_status"]
    print(f" -> Gateway Server A status: {server_a_state}")
    assert server_a_state == "OFFLINE", f"Expected OFFLINE, got {server_a_state}"

    # Try adding Fiction book (which normally goes to Server A)
    failover_add = requests.post(f"{GATEWAY_URL}/add_book", json={
        "title": "Failover Book During Outage",
        "genre": "Fantasy",
        "author": "Anonymous"
    })
    assert failover_add.status_code == 201, f"Expected failover to succeed, got {failover_add.status_code}: {failover_add.text}"
    fo_data = failover_add.json()
    print(f" -> Added book during Server A outage. Routed to: {fo_data.get('routed_server')} (Reason: {fo_data.get('routing_reason')})")
    assert "Server B" in fo_data.get("routed_server", ""), "Should have failed over to Server B!"
    print("  [OK] PASS: Test 3 Server failure detected & automatic failover to Server B succeeded!")

    # ---------------- TEST 4: Server Recovery ---------------- #
    print("\n[TEST 4] Testing Server Recovery...")
    rec_res = requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_a"}).json()
    print(f" -> Recovery triggered: {rec_res['message']}")
    time.sleep(1.0)
    gw_status2 = requests.get(f"{GATEWAY_URL}/server_status").json()
    server_a_rec = gw_status2["servers"]["5001"]["detailed_status"]
    print(f" -> Gateway Server A status: {server_a_rec}")
    assert server_a_rec in ("RECOVERED", "HEALTHY", "active")
    print("  [OK] PASS: Test 4 Server recovery detected and Server A returned to routing pool!")

    # ---------------- TEST 5: Distributed Search ---------------- #
    print("\n[TEST 5] Testing Distributed Search Across Nodes...")
    search_res = requests.get(f"{GATEWAY_URL}/search", params={"query": "Algorithms"}).json()
    print(f" -> Search for 'Algorithms' found {len(search_res)} book(s): {[b['title'] for b in search_res]}")
    assert len(search_res) >= 1

    search_all = requests.get(f"{GATEWAY_URL}/books").json()
    print(f" -> Total books merged across distributed nodes: {len(search_all)}")
    genres_found = set(b["genre"] for b in search_all)
    print(f" -> Genres present: {genres_found}")
    assert "Fiction" in genres_found or "Fantasy" in genres_found
    assert "Science" in genres_found or "Technology" in genres_found
    print("  [OK] PASS: Test 5 Distributed search and catalog merge succeeded!")

    # ---------------- TEST 6: Issue and Return Flow ---------------- #
    print("\n[TEST 6] Testing Complete Issue & Return Flow...")
    # Find an available book
    avail_book = next((b for b in search_all if b["availability"] == "Available"), None)
    assert avail_book is not None, "No available book found to test issue"
    test_book_id = avail_book["id"]
    print(f" -> Selected book for loan: '{avail_book['title']}' (ID: {test_book_id})")

    # Issue book
    issue_res = requests.post(f"{GATEWAY_URL}/books/{test_book_id}/issue", json={
        "username": "student",
        "due_days": 14
    })
    assert issue_res.status_code == 200, f"Issue failed: {issue_res.text}"
    issue_data = issue_res.json()
    print(f" -> Book issued successfully: {issue_data['message']}")
    assert issue_data["book"]["availability"] == "Issued"

    # Verify student shelf has it
    my_books = requests.get(f"{GATEWAY_URL}/my_books?username=student").json()
    my_book_ids = [b["book_id"] for b in my_books]
    assert test_book_id in my_book_ids, f"Book {test_book_id} not in student shelf"
    print(f" -> Verified book appears in student's issued shelf ({len(my_books)} issued books)")

    # Return book
    return_res = requests.post(f"{GATEWAY_URL}/books/{test_book_id}/return")
    assert return_res.status_code == 200, f"Return failed: {return_res.text}"
    ret_data = return_res.json()
    print(f" -> Book returned: {ret_data['message']}, Fine: INR {ret_data['fine_amount']}")
    assert ret_data["book"]["availability"] == "Available"
    print("  [OK] PASS: Test 6 Complete Issue/Return lifecycle verified!")

    # ---------------- TEST 7: Overdue & Fine Calculation ---------------- #
    print("\n[TEST 7] Testing Overdue Book & Fine Calculation...")
    from models import calculate_fine
    # Test past due date: 5 days ago
    past_due_date = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    days, fine = calculate_fine(past_due_date, daily_fine_rate=10.0)
    print(f" -> Past due date ({past_due_date}): Overdue by {days} days, fine calculated: INR {fine}")
    assert days == 5
    assert fine == 50.0

    # Test future due date: 7 days from now
    future_due_date = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    days_f, fine_f = calculate_fine(future_due_date, daily_fine_rate=10.0)
    print(f" -> Future due date ({future_due_date}): Overdue by {days_f} days, fine: INR {fine_f}")
    assert days_f == 0 and fine_f == 0.0
    print("  [OK] PASS: Test 7 Fine calculation logic validated!")

    # ---------------- TEST 8: Analytics Dashboard ---------------- #
    print("\n[TEST 8] Testing Real-Time Analytics Endpoint...")
    analytics = requests.get(f"{GATEWAY_URL}/analytics").json()
    print(f" -> Library Stats: {analytics['library_stats']}")
    print(f" -> Top Popular Books: {[b['title'] for b in analytics['popular_books']]}")
    print(f" -> Distributed Node Metrics: Server A Share: {analytics['distributed_metrics']['server_a']['share_percent']}%, Server B Share: {analytics['distributed_metrics']['server_b']['share_percent']}%")
    assert "total_books" in analytics["library_stats"]
    assert "server_a" in analytics["distributed_metrics"]
    assert "server_b" in analytics["distributed_metrics"]
    print("  [OK] PASS: Test 8 Analytics metrics verified!")

    # ---------------- TEST 9: Authentication & Roles ---------------- #
    print("\n[TEST 9] Testing Authentication & Role-Based Access...")
    # Login as student
    login_s = requests.post(f"{GATEWAY_URL}/api/login", json={"username": "student", "password": "student123"}).json()
    assert "token" in login_s
    assert login_s["user"]["role"] == "student"
    print(f" -> Student login OK: {login_s['user']['name']} ({login_s['user']['role']})")

    # Login as admin
    login_a = requests.post(f"{GATEWAY_URL}/api/login", json={"username": "admin", "password": "admin123"}).json()
    assert "token" in login_a
    assert login_a["user"]["role"] == "admin"
    print(f" -> Admin login OK: {login_a['user']['name']} ({login_a['user']['role']})")

    # Check user management endpoint with admin token
    users_list = requests.get(f"{GATEWAY_URL}/api/users", headers={"Authorization": f"Bearer {login_a['token']}"}).json()
    print(f" -> Admin retrieved {len(users_list)} registered users: {[u['username'] for u in users_list]}")
    assert len(users_list) >= 2
    print("  [OK] PASS: Test 9 Authentication and role validation succeeded!")

    print("\n==================================================")
    print("SUCCESS: ALL 9 TEST SUITES PASSED WITH 100% SUCCESS!")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
