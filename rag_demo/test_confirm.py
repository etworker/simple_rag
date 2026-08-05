import requests, time, sys, glob, os

BASE = "http://127.0.0.1:8199"

# Wait for server
for _ in range(10):
    try:
        requests.get(f"{BASE}/api/documents/list", timeout=2)
        break
    except:
        time.sleep(1)
else:
    print("Server not running")
    sys.exit(1)

print("=== Reset ===")
requests.post(f"{BASE}/api/documents/clear")
print("Cleared")

print("\n=== Upload ===")
pdfs = glob.glob(r"c:\Users\0937\Documents\work\simple_rag\rag_demo\uploads\**\*.pdf", recursive=True)
if not pdfs:
    print("No PDF found")
    sys.exit(1)
filepath = pdfs[0]
filename = os.path.basename(filepath)
print(f"File: {filename}")
with open(filepath, "rb") as f:
    r = requests.post(f"{BASE}/api/documents/upload", files={"file": (filename, f)})
data = r.json()
task_id = data["task_id"]
print(f"Task: {task_id}")

print("\n=== Wait for review ===")
for i in range(40):
    r = requests.get(f"{BASE}/api/documents/review/active")
    d = r.json()
    if d.get("status") == "done":
        print(f"Done! safe={d['result']['is_safe']}")
        break
    print(f"  [{i}] {d.get('status')} {d.get('current_step','')}")
    time.sleep(3)

print("\n=== Confirm ===")
r = requests.post(f"{BASE}/api/documents/review/{task_id}/confirm")
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")

print("\n=== Verify ===")
r = requests.get(f"{BASE}/api/documents/list")
docs = r.json()["documents"]
print(f"Docs: {len(docs)}")
for d in docs:
    print(f"  - {d['filename']} ({d.get('paragraph_count',0)} paras)")
