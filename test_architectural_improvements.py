import requests
import json
import time
import datetime

GATEWAY_URL = "http://localhost:5000"
SERVER_A_URL = "http://localhost:5001"
SERVER_B_URL = "http://localhost:5002"

def test_all():
    print("==================================================")
    print("TESTING 10 ARCHITECTURAL IMPROVEMENT SCENARIOS")
    print("==================================================")

    # 0. Clean state
    requests.post(f"{GATEWAY_URL}/api/reset_simulation")
    time.sleep(1.0)

    # ---------------- TEST 1: Normal Primary Routing ---------------- #
    print("\n[TEST 1] Normal Primary Routing (Adventure: A healthy, B healthy)...")
    res1 = requests.get(f"{GATEWAY_URL}/books", params={"genre": "Adventure"})
    assert res1.status_code == 200, f"Expected 200, got {res1.status_code}"
    routed_srv1 = res1.headers.get("X-Routed-Server")
    role1 = res1.headers.get("X-Server-Role")
    reason1 = res1.headers.get("X-Routing-Reason")
    books1 = res1.json()
    print(f" -> Routed to: {routed_srv1} (Role: {role1}, Reason: {reason1})")
    print(f" -> Returned {len(books1)} Adventure book(s)")
    assert "Server A" in routed_srv1, f"Expected Server A, got {routed_srv1}"
    assert role1 == "PRIMARY", f"Expected role PRIMARY, got {role1}"
    print(" [OK] PASS: Test 1 Normal primary routing succeeded! Adventure routed to Primary Server A.")

    # ---------------- TEST 2: Load-Aware Routing ---------------- #
    print("\n[TEST 2] Load-Aware Routing (Adventure: A heavily loaded, B lightly loaded)...")
    # Spike load on Server A
    requests.post(f"{GATEWAY_URL}/api/simulate_load", json={"server": "server_a"})
    time.sleep(0.8)

    # Verify score on A is significantly higher than B
    stat = requests.get(f"{GATEWAY_URL}/server_status").json()
    score_a = stat["servers"]["5001"]["routing_score"]
    score_b = stat["servers"]["5002"]["routing_score"]
    print(f" -> Server A Score: {score_a} | Server B Score: {score_b}")
    assert score_a > score_b + 20, f"Expected Server A score ({score_a}) to be significantly higher than Server B ({score_b})"

    # Read Adventure books
    res2 = requests.get(f"{GATEWAY_URL}/books", params={"genre": "Adventure"})
    assert res2.status_code == 200, f"Expected 200, got {res2.status_code}"
    routed_srv2 = res2.headers.get("X-Routed-Server")
    role2 = res2.headers.get("X-Server-Role")
    reason2 = res2.headers.get("X-Routing-Reason")
    books2 = res2.json()
    print(f" -> Routed to: {routed_srv2} (Role: {role2}, Reason: {reason2})")
    print(f" -> Returned {len(books2)} Adventure book(s) from {routed_srv2}")
    assert "Server B" in routed_srv2, f"Expected Server B (Replica) to be selected due to heavy load on A, got {routed_srv2}"
    assert role2 == "REPLICA", f"Expected role REPLICA, got {role2}"
    print(" [OK] PASS: Test 2 Load-aware routing succeeded! Gateway offloaded Adventure READ to Server B replica.")

    # Reset load simulation
    requests.post(f"{GATEWAY_URL}/api/reset_simulation")
    time.sleep(0.8)

    # ---------------- TEST 3: Primary Failure (Automatic Failover) ---------------- #
    print("\n[TEST 3] Primary Failure (Adventure: A OFFLINE, B HEALTHY)...")
    # Take Server A offline
    requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_a"})
    time.sleep(0.8)

    stat3 = requests.get(f"{GATEWAY_URL}/server_status").json()
    assert stat3["servers"]["5001"]["detailed_status"] == "OFFLINE", "Server A should be OFFLINE"

    # Read Adventure books
    res3 = requests.get(f"{GATEWAY_URL}/books", params={"genre": "Adventure"})
    assert res3.status_code == 200, f"Expected 200 from replica, got {res3.status_code}"
    routed_srv3 = res3.headers.get("X-Routed-Server")
    role3 = res3.headers.get("X-Server-Role")
    reason3 = res3.headers.get("X-Routing-Reason")
    books3 = res3.json()
    print(f" -> Routed to: {routed_srv3} (Role: {role3}, Reason: {reason3})")
    print(f" -> Returned {len(books3)} Adventure book(s) from Server B replica")
    assert "Server B" in routed_srv3, f"Expected Server B, got {routed_srv3}"
    assert role3 == "REPLICA", f"Expected role REPLICA, got {role3}"
    assert len(books3) > 0, "Server B must actually have Adventure replica data!"
    print(" [OK] PASS: Test 3 Primary failure automatic failover succeeded! Server B replica returned Adventure data.")

    # Recover Server A
    requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_a"})
    time.sleep(0.8)

    # ---------------- TEST 4: Replica Failure ---------------- #
    print("\n[TEST 4] Replica Failure (Adventure: A HEALTHY, B OFFLINE)...")
    # Take Server B offline
    requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_b"})
    time.sleep(0.8)

    stat4 = requests.get(f"{GATEWAY_URL}/server_status").json()
    assert stat4["servers"]["5002"]["detailed_status"] == "OFFLINE", "Server B should be OFFLINE"

    res4 = requests.get(f"{GATEWAY_URL}/books", params={"genre": "Adventure"})
    assert res4.status_code == 200, f"Expected 200, got {res4.status_code}"
    routed_srv4 = res4.headers.get("X-Routed-Server")
    role4 = res4.headers.get("X-Server-Role")
    reason4 = res4.headers.get("X-Routing-Reason")
    print(f" -> Routed to: {routed_srv4} (Role: {role4}, Reason: {reason4})")
    assert "Server A" in routed_srv4, f"Expected Server A, got {routed_srv4}"
    assert role4 == "PRIMARY", f"Expected role PRIMARY, got {role4}"
    print(" [OK] PASS: Test 4 Replica failure succeeded! Gateway routed to healthy Primary Server A.")

    # Recover Server B
    requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_b"})
    time.sleep(0.8)

    # ---------------- TEST 5: Both Servers Unavailable ---------------- #
    print("\n[TEST 5] Both Servers Unavailable (Adventure: A OFFLINE, B OFFLINE)...")
    requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_a"})
    requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_b"})
    time.sleep(0.8)

    res5 = requests.get(f"{GATEWAY_URL}/books", params={"genre": "Adventure"})
    print(f" -> Response Status: {res5.status_code}, Body: {res5.text}")
    assert res5.status_code == 503, f"Expected 503 Service Unavailable, got {res5.status_code}"
    # Verify Gateway is still healthy and responsive
    gw_alive = requests.get(f"{GATEWAY_URL}/server_status")
    assert gw_alive.status_code == 200, "Gateway must remain alive and responsive!"
    print(" [OK] PASS: Test 5 Both servers unavailable returned clean 503 and Gateway remained operational.")

    # Reset both to healthy
    requests.post(f"{GATEWAY_URL}/api/reset_simulation")
    time.sleep(0.8)

    # ---------------- TEST 6: Write Replication ---------------- #
    print("\n[TEST 6] Write Replication (Add new Adventure book)...")
    test_title = f"Test Adventure Odyssey {int(time.time())}"
    add_res = requests.post(f"{GATEWAY_URL}/add_book", json={
        "title": test_title,
        "genre": "Adventure",
        "author": "Explorer John"
    })
    assert add_res.status_code == 201, f"Add book failed: {add_res.text}"
    new_book = add_res.json()
    book_id = new_book["id"]
    print(f" -> Created book '{test_title}' with ID: {book_id}")
    print(f" -> Gateway routed to: {new_book.get('routed_server')} (Role: {new_book.get('server_role')})")
    print(f" -> Replicated to: {new_book.get('replicated_to')}")
    assert "Server A" in new_book.get("routed_server", "")

    time.sleep(0.5)

    # Verify book exists on Server A directly as PRIMARY
    srv_a_books = requests.get(f"{SERVER_A_URL}/books/{book_id}").json()
    assert srv_a_books["id"] == book_id
    assert srv_a_books["data_role"] == "PRIMARY"
    print(f" -> Verified on Server A: ID={srv_a_books['id']}, role={srv_a_books['data_role']}")

    # Verify book exists on Server B directly as REPLICA
    srv_b_books = requests.get(f"{SERVER_B_URL}/books/{book_id}").json()
    assert srv_b_books["id"] == book_id
    assert srv_b_books["data_role"] == "REPLICA"
    print(f" -> Verified on Server B: ID={srv_b_books['id']}, role={srv_b_books['data_role']}")
    print(" [OK] PASS: Test 6 Write replication succeeded! New book exists on Primary A and Replica B.")

    # ---------------- TEST 7: Update Replication ---------------- #
    print("\n[TEST 7] Update Replication (Edit Adventure book)...")
    updated_title = f"{test_title} - Special Edition"
    # Admin login for auth header
    login_a = requests.post(f"{GATEWAY_URL}/api/login", json={"username": "admin", "password": "admin123"}).json()
    token = login_a["token"]

    up_res = requests.put(
        f"{GATEWAY_URL}/books/{book_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": updated_title, "genre": "Adventure", "author": "Explorer John Updated"}
    )
    assert up_res.status_code == 200, f"Update failed: {up_res.text}"
    time.sleep(0.5)

    # Verify Server A has updated title
    chk_a = requests.get(f"{SERVER_A_URL}/books/{book_id}").json()
    assert chk_a["title"] == updated_title, f"Server A title mismatch: {chk_a['title']}"
    print(f" -> Server A primary updated: '{chk_a['title']}'")

    # Verify Server B replica has updated title
    chk_b = requests.get(f"{SERVER_B_URL}/books/{book_id}").json()
    assert chk_b["title"] == updated_title, f"Server B replica title mismatch: {chk_b['title']}"
    print(f" -> Server B replica updated: '{chk_b['title']}'")
    print(" [OK] PASS: Test 7 Update replication succeeded! Primary and replica reflect updated data.")

    # ---------------- TEST 8: Delete Replication ---------------- #
    print("\n[TEST 8] Delete Replication (Delete Adventure book)...")
    del_res = requests.delete(
        f"{GATEWAY_URL}/books/{book_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert del_res.status_code == 200, f"Delete failed: {del_res.text}"
    time.sleep(0.5)

    # Verify book deleted on Server A
    chk_a_del = requests.get(f"{SERVER_A_URL}/books/{book_id}")
    assert chk_a_del.status_code == 404, f"Expected 404 on Server A, got {chk_a_del.status_code}"
    print(" -> Confirmed deleted on Server A")

    # Verify book deleted on Server B replica
    chk_b_del = requests.get(f"{SERVER_B_URL}/books/{book_id}")
    assert chk_b_del.status_code == 404, f"Expected 404 on Server B replica, got {chk_b_del.status_code}"
    print(" -> Confirmed deleted on Server B replica")
    print(" [OK] PASS: Test 8 Delete replication succeeded! Book removed from both nodes.")

    # ---------------- TEST 9: Issue/Return Consistency ---------------- #
    print("\n[TEST 9] Issue/Return State Consistency...")
    target_id = "bk-a-01"  # The Hobbit

    # Issue book through Gateway
    iss_res = requests.post(f"{GATEWAY_URL}/books/{target_id}/issue", json={
        "username": "student",
        "due_days": 10
    })
    assert iss_res.status_code == 200, f"Issue failed: {iss_res.text}"
    iss_data = iss_res.json()["book"]
    time.sleep(0.5)

    # Verify Server A (Primary) state
    chk_a_iss = requests.get(f"{SERVER_A_URL}/books/{target_id}").json()
    assert chk_a_iss["availability"] == "Issued"
    assert chk_a_iss["issued_to"] == "student"

    # Verify Server B (Replica) state
    chk_b_iss = requests.get(f"{SERVER_B_URL}/books/{target_id}").json()
    assert chk_b_iss["availability"] == "Issued", f"Server B replica availability: {chk_b_iss['availability']}"
    assert chk_b_iss["issued_to"] == "student", f"Server B replica issued_to: {chk_b_iss['issued_to']}"
    assert chk_b_iss["issue_count"] == chk_a_iss["issue_count"], "Issue counts must match"
    print(f" -> Issued: Server A={chk_a_iss['availability']}, Server B Replica={chk_b_iss['availability']}")

    # Return book through Gateway
    ret_res = requests.post(f"{GATEWAY_URL}/books/{target_id}/return")
    assert ret_res.status_code == 200, f"Return failed: {ret_res.text}"
    time.sleep(0.5)

    chk_a_ret = requests.get(f"{SERVER_A_URL}/books/{target_id}").json()
    chk_b_ret = requests.get(f"{SERVER_B_URL}/books/{target_id}").json()
    assert chk_a_ret["availability"] == "Available"
    assert chk_b_ret["availability"] == "Available"
    assert chk_a_ret["issued_to"] is None
    assert chk_b_ret["issued_to"] is None
    print(f" -> Returned: Server A={chk_a_ret['availability']}, Server B Replica={chk_b_ret['availability']}")
    print(" [OK] PASS: Test 9 Issue/return consistency verified! Both primary and replica stay in sync.")

    # ---------------- TEST 10: Recovery & Synchronization ---------------- #
    print("\n[TEST 10] Server Recovery & Re-admission...")
    # Step 1: Take Server A offline
    requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_a"})
    time.sleep(0.8)

    # Serve Adventure through Server B
    res_adv = requests.get(f"{GATEWAY_URL}/books", params={"genre": "Adventure"})
    assert res_adv.status_code == 200
    assert "Server B" in res_adv.headers.get("X-Routed-Server", "")
    print(" -> Adventure served through Server B while Server A was OFFLINE")

    # Step 2: Recover Server A
    rec_res = requests.post(f"{GATEWAY_URL}/api/simulate_failover", json={"server": "server_a"})
    time.sleep(1.0)

    # Check Gateway status
    gw_stat = requests.get(f"{GATEWAY_URL}/server_status").json()
    state_a = gw_stat["servers"]["5001"]["detailed_status"]
    print(f" -> Gateway detected Server A state: {state_a}")
    assert state_a in ("HEALTHY", "RECOVERED"), f"Expected HEALTHY or RECOVERED, got {state_a}"

    # Verify normal primary preference restored for Adventure
    res_post_rec = requests.get(f"{GATEWAY_URL}/books", params={"genre": "Adventure"})
    assert res_post_rec.status_code == 200
    routed_post = res_post_rec.headers.get("X-Routed-Server")
    role_post = res_post_rec.headers.get("X-Server-Role")
    print(f" -> Post-recovery routed to: {routed_post} (Role: {role_post})")
    assert "Server A" in routed_post, f"Expected Server A restored as preferred primary, got {routed_post}"
    assert role_post == "PRIMARY"
    print(" [OK] PASS: Test 10 Recovery verified! Server A recovered, synchronized, and re-admitted as primary.")

    print("\n==================================================")
    print("SUCCESS: ALL 10 ARCHITECTURAL TESTS PASSED (100% SUCCESS)!")
    print("==================================================")

if __name__ == "__main__":
    test_all()
