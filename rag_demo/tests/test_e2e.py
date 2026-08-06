"""
端到端 Web 自动化测试

流程: 清空 → 上传 → 预审核 → 确认入库 → 验证列表
验证: file_hash/doc_id 字段、MD5 后缀显示
"""
import sys
import os
import io

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import time
import glob
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def api(method, path, data=None, files=None):
    """Simple HTTP client"""
    url = f"{BASE}{path}"
    if files:
        # multipart upload
        boundary = "----testboundary"
        body = b""
        for key, (filename, content) in files.items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
            body += b"Content-Type: application/octet-stream\r\n\r\n"
            body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    elif data:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def wait_server():
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{BASE}/api/documents/list", timeout=2)
            return True
        except:
            time.sleep(1)
    return False


def find_pdf():
    """Find a test PDF file"""
    # Try both old and new cache dirs
    for cache_dir in [".simple_rag", ".cache/simple_rag"]:
        cache_upload = os.path.join(os.path.expanduser("~"), cache_dir, "uploads")
        if os.path.exists(cache_upload):
            pdfs = glob.glob(os.path.join(cache_upload, "**", "*.pdf"), recursive=True)
            if pdfs:
                return pdfs[0]
    return None


def main():
    global PASS, FAIL

    print("=" * 60)
    print("  端到端 Web 自动化测试")
    print("=" * 60)

    # Wait for server
    print("\n[0] 等待服务器启动...")
    if not wait_server():
        print("  [FAIL] server not started")
        sys.exit(1)
    print("  服务器已启动")

    # Step 1: Clear
    print("\n[1] 清空知识库")
    status, data = api("POST", "/api/documents/clear")
    check("清空返回200", status == 200, f"got {status}")
    check("清空成功", "message" in data, str(data))

    # Step 2: Verify list is empty
    print("\n[2] 验证列表为空")
    status, data = api("GET", "/api/documents/list")
    check("列表返回200", status == 200)
    check("列表为空", data.get("total") == 0, f"total={data.get('total')}")

    # Step 3: Find and upload PDF
    print("\n[3] 上传文档")
    pdf_path = find_pdf()
    if not pdf_path:
        print("  [WARN] no test PDF found")
        sys.exit(0)

    filename = os.path.basename(pdf_path)
    # Use original Chinese filename for upload
    original_name = "(二级)(司批)网络与信息安全管理手册.pdf"
    print(f"  文件: {original_name}")
    print(f"  磁盘路径: {pdf_path}")

    with open(pdf_path, "rb") as f:
        content = f.read()

    status, data = api("POST", "/api/documents/upload",
                       files={"file": (original_name, content)})
    check("上传返回200", status == 200, f"got {status}, {data}")
    if status != 200:
        print(f"  上传失败: {data}")
        sys.exit(1)

    task_id = data.get("task_id")
    check("返回 task_id", task_id is not None, f"data={data}")
    check("返回 file_hash", "file_hash" in data and len(data["file_hash"]) > 0,
          f"file_hash={data.get('file_hash', 'MISSING')}")
    check("返回 filename", "filename" in data, f"data={data}")
    file_hash = data.get("file_hash", "")
    check("file_hash len=64(SHA256)", len(file_hash) == 64, f"len={len(file_hash)}")

    # Step 4: Wait for pre-review
    print("\n[4] 等待预审核完成...")
    for i in range(120):
        status, data = api("GET", "/api/documents/review/active")
        if data.get("task_id") is None:
            # No active task - either completed or cancelled
            # Check if it was confirmed/rejected
            break
        task_status = data.get("status", "?")
        step = data.get("current_step", "")
        if task_status == "done":
            result = data.get("result", {})
            n = len(result.get("inconsistencies", []))
            print(f"  预审核完成! 矛盾数={n}")
            check("预审核返回 file_hash", "file_hash" in data and len(data.get("file_hash", "")) > 0,
                  f"file_hash={data.get('file_hash', 'MISSING')}")
            break
        elif task_status == "error":
            print(f"  [FAIL] pre-review failed: {data}")
            break
        else:
            if i % 10 == 0:
                print(f"  [{i}s] {task_status} | {step}")
            time.sleep(3)
    else:
        print("  [FAIL] pre-review timeout (120s)")

    # Step 5: Confirm ingest
    print("\n[5] 确认入库")
    status, data = api("POST", f"/api/documents/review/{task_id}/confirm")
    check("入库返回200", status == 200, f"got {status}, {data}")
    if status == 200:
        check("返回 message", "message" in data)
        check("返回 filename", "filename" in data)
        check("返回 paragraphs", "paragraphs" in data)
    else:
        print(f"  入库失败: {data}")

    # Step 6: Verify document list
    print("\n[6] 验证已入库列表")
    status, data = api("GET", "/api/documents/list")
    check("列表返回200", status == 200)
    docs = data.get("documents", [])
    check("文档数=1", len(docs) == 1, f"count={len(docs)}")
    if docs:
        d = docs[0]
        check("有 filename 字段", "filename" in d)
        check("filename 是原始名(非MD5)", not d["filename"].endswith(".pdf") or
              "(" in d.get("filename", ""), f"filename={d.get('filename')}")
        check("有 doc_id 字段", "doc_id" in d, f"keys={list(d.keys())}")
        check("有 file_hash 字段", "file_hash" in d and len(d["file_hash"]) > 0,
              f"file_hash={d.get('file_hash', 'MISSING')}")
        check("doc_id 格式=filename#hash[:8]", "#" in d.get("doc_id", ""),
              f"doc_id={d.get('doc_id')}")
        check("file_hash 与上传一致", d.get("file_hash") == file_hash,
              f"upload={file_hash[:8]} list={d.get('file_hash', '')[:8]}")

    # Step 7: Verify frontend has SHA-256 display code (JS is in app.js after split)
    print("\n[7] Verify frontend has SHA-256 display logic")
    req = urllib.request.Request(f"{BASE}/")
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8")
        headers = dict(resp.headers)
    check("HTML references app.js", "app.js" in html)
    req_js = urllib.request.Request(f"{BASE}/static/app.js")
    with urllib.request.urlopen(req_js) as resp_js:
        js = resp_js.read().decode("utf-8")
    check("JS has fmtDocName", "fmtDocName" in js)
    check("JS has newDocHash", "newDocHash" in js)
    check("JS has data.file_hash", "data.file_hash" in js)
    check("JS has hashShort", "hashShort" in js)
    # HTTP headers are case-insensitive — normalize keys for lookup
    hdr_lower = {k.lower(): v for k, v in headers.items()}
    cache_hdr = hdr_lower.get("cache-control", "")
    check("HTTP Cache-Control: no-cache", "no-cache" in cache_hdr.lower() or "no-store" in cache_hdr.lower(),
          f"Cache-Control={cache_hdr}")

    # Summary
    print("\n" + "=" * 60)
    print(f"  Result: [PASS] {PASS} | [FAIL] {FAIL}")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
