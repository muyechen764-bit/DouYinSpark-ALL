"""签名器 — 管理浏览器实例，提供 API 调用和页面操作"""
import time
from playwright.sync_api import sync_playwright

import os as _os

def _find_chrome():
    """定位 Playwright 安装的 Chromium"""
    import glob
    base = _os.path.expanduser("~/AppData/Local/ms-playwright")
    if _os.path.exists(base):
        # 找最新版本的 chromium
        dirs = sorted(glob.glob(base + "/chromium-*"), reverse=True)
        for d in dirs:
            exe = d + "/chrome-win64/chrome.exe"
            if _os.path.exists(exe):
                return exe
    return None

CHROME_EXE = _find_chrome()


class Signer:
    def __init__(self, cookies):
        for c in cookies:
            if "sameSite" in c:
                del c["sameSite"]
        self._pw = sync_playwright().start()
        # 优先用 Playwright 默认启动，失败则用自定义路径
        try:
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception:
            if CHROME_EXE:
                self._browser = self._pw.chromium.launch(
                    headless=True, executable_path=CHROME_EXE
                )
            else:
                raise
        ctx = self._browser.new_context(viewport={"width": 1280, "height": 2400})
        ctx.set_default_navigation_timeout(120000)
        ctx.set_default_timeout(120000)
        page = ctx.new_page()
        ctx.add_cookies(cookies)
        page.goto(
            "https://creator.douyin.com/creator-micro/data/following/chat",
            timeout=60000,
        )
        page.wait_for_timeout(5000)
        self.page = page

    def api_fetch(self, method, path, params=None, body=None):
        """通过浏览器内 fetch() 调用 API。仅用于不需要 a_bogus 的简单端点"""
        import urllib.parse
        url = "https://creator.douyin.com" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        arg = {"method": method, "url": url, "body": body}
        js = """
        async (arg) => {
            const o = {method: arg.method, headers: {'Accept':'application/json'}, credentials: 'include'};
            if (arg.body) { o.body = JSON.stringify(arg.body); o.headers['Content-Type'] = 'application/json'; }
            const r = await fetch(arg.url, o);
            const t = await r.text();
            try { return JSON.parse(t); }
            catch (e) { return {_status: r.status, _text: t.substring(0,300)}; }
        }
        """
        return self.page.evaluate(js, arg)

    def close(self):
        self._browser.close()
        self._pw.stop()
