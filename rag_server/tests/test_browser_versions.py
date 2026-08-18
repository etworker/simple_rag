"""真实浏览器 smoke test：验证同文档多版本的管理交互。"""

import os
import urllib.error
import urllib.request

import pytest

BASE_URL = os.environ.get("RAG_BROWSER_BASE_URL", "http://127.0.0.1:8000")
_BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def _browser_path() -> str | None:
    return next((path for path in _BROWSER_PATHS if os.path.exists(path)), None)


def _server_is_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/documents/list", timeout=3):
            return True
    except (OSError, urllib.error.URLError):
        return False


def test_version_management_in_real_browser():
    """打开真实页面，切换主版本并验证同名上传提示。"""
    if not _server_is_reachable():
        pytest.skip(f"浏览器测试服务未启动: {BASE_URL}")
    browser_path = _browser_path()
    if not browser_path:
        pytest.skip("未找到 Chrome 或 Edge 浏览器")

    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        pytest.skip("未安装 playwright")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=browser_path)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        original_active_doc_id = None
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded")
            expect(page.locator("#docList")).to_be_visible()
            page.wait_for_timeout(500)

            data = page.request.get(f"{BASE_URL}/api/documents/list").json()
            docs = data.get("documents", [])
            pairs = {}
            for doc in docs:
                family_id = doc.get("family_id") or doc.get("filename")
                pairs.setdefault(family_id, []).append(doc)
            pair = next(
                (
                    items
                    for items in pairs.values()
                    if any(item.get("status") == "active" for item in items)
                    and any(item.get("status") == "inactive" for item in items)
                ),
                None,
            )
            if not pair:
                pytest.skip("当前测试库没有 active/inactive 同文档版本对")

            active = next(item for item in pair if item.get("status") == "active")
            inactive = next(item for item in pair if item.get("status") == "inactive")
            original_active_doc_id = active["doc_id"]

            inactive_item = page.locator(".doc-item.inactive").filter(has_text=inactive.get("label", "")).first
            expect(inactive_item).to_be_visible()
            expect(inactive_item.locator(".doc-status-tag")).to_contain_text("历史版本")
            expect(inactive_item.locator("button.primary-doc-btn")).to_have_text("设为当前")

            page.on("dialog", lambda dialog: dialog.accept())
            with page.expect_response(
                lambda response: response.url.endswith("/api/documents/primary")
                and response.request.method == "POST"
            ):
                inactive_item.locator("button.primary-doc-btn").click()
            page.wait_for_function(
                """
                (docId) => Array.from(document.querySelectorAll('.doc-item'))
                    .some((item) => item.dataset.docId === docId &&
                        item.querySelector('.doc-status-tag.primary'))
                """,
                arg=inactive["doc_id"],
            )

            switched = page.request.get(f"{BASE_URL}/api/documents/list").json()["documents"]
            switched_by_id = {item["doc_id"]: item for item in switched}
            assert switched_by_id[inactive["doc_id"]]["status"] == "active"
            assert switched_by_id[active["doc_id"]]["status"] == "inactive"

            # 恢复测试前的主版本，避免浏览器 smoke test 改变开发库状态。
            restore = page.request.post(
                f"{BASE_URL}/api/documents/primary",
                form={"doc_id": active["doc_id"]},
            )
            assert restore.ok
            page.reload(wait_until="domcontentloaded")
            expect(page.locator("#docList")).to_be_visible()

            # 上传同名但内容不同的文件，只验证前端弹出版本提示，然后取消上传。
            file_input = page.locator("#fileInput")
            file_input.set_input_files(
                {
                    "name": active["filename"],
                    "mimeType": "application/pdf",
                    "buffer": b"browser-version-smoke-test",
                }
            )
            expect(page.locator("#modalInputWrap")).to_be_visible(timeout=30_000)
            page.locator("#modalBtnYes").click()
            expect(page.locator("#modalMsg")).to_contain_text("疑似同一文档", timeout=30_000)
            page.locator("#modalBtnNo").click()
        finally:
            try:
                if original_active_doc_id:
                    restore = page.request.post(
                        f"{BASE_URL}/api/documents/primary",
                        form={"doc_id": original_active_doc_id},
                    )
                    assert restore.ok
            finally:
                browser.close()
