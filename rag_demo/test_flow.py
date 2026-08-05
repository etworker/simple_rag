import requests, time, sys, glob, os

BASE = "http://127.0.0.1:8199"

print("=== Step 1: Reset ===")
r = requests.post(f"{BASE}/api/documents/clear")
print(f"Clear: {r.json().get('message','?')}")

r = requests.get(f"{BASE}/api/documents/list")
print(f"Docs after clear: {len(r.json().get('documents', []))}")

print("\n=== Step 2: Upload ===")
pdfs = glob.glob(r"c:\Users\0937\Documents\work\simple_rag\rag_demo\uploads\**\*.pdf", recursive=True)
if not pdfs:
    print("No test file found")
    sys.exit(1)
filepath = pdfs[0]
filename = os.path.basename(filepath)
print(f"File: {filename} ({os.path.getsize(filepath)} bytes)")

with open(filepath, "rb") as f:
    r = requests.post(f"{BASE}/api/documents/upload", files={"file": (filename, f)})
print(f"Upload status: {r.status_code}")
data = r.json()
print(f"Upload response: {data}")
task_id = data.get("task_id")
if not task_id:
    print("ERROR: No task_id")
    sys.exit(1)

print(f"\nTask ID: {task_id}")

print("\n=== Step 3: Wait for pre-review ===")
for i in range(60):
    r = requests.get(f"{BASE}/api/documents/review/active")
    d = r.json()
    status = d.get("status", "?")
    step = d.get("current_step", "")
    if status == "done":
        result = d.get("result", {})
        print(f"Done! safe={result.get('is_safe')} conflicts={len(result.get('inconsistencies', []))}")
        break
    elif status == "error":
        print(f"ERROR: {d}")
        break
    else:
        print(f"  [{i}] status={status} step={step}")
        time.sleep(3)

print("\n=== Step 4: Confirm ===")
r = requests.post(f"{BASE}/api/documents/review/{task_id}/confirm")
print(f"Confirm status: {r.status_code}")
if r.status_code == 200:
    print(f"Confirm result: {r.json()}")
else:
    print(f"Confirm FAILED: {r.text}")

print("\n=== Step 5: Verify ===")
r = requests.get(f"{BASE}/api/documents/list")
docs = r.json().get("documents", [])
print(f"Docs after confirm: {len(docs)}")
for d in docs:
    print(f"  - {d['filename']} ({d.get('paragraph_count', '?')} paras)")
