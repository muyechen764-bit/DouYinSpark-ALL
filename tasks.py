"""主任务 — 边滚动好友列表边匹配目标，找到立即发消息"""
import os
import traceback
import json
import time
from core.signer import Signer
from core.msg_builder import build_message
from utils.logger import setup_logger
from utils.config import get_config, get_userData

config = get_config()
userData = get_userData()
logger = setup_logger(level=config.get("logLevel", "Info"))
matchMode = config.get("matchMode", "nickname")
groupMatchMode = config.get("groupMatchMode", "name")


_SCROLL_CONTAINER_JS = """
() => {
    var els = document.querySelectorAll('[class*="semi-list"], #sub-app ul');
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (el && el.scrollTop + el.clientHeight < el.scrollHeight - 10) {
            el.scrollTop += 600; return true;
        }
    }
    var w = document.scrollingElement;
    if (w && w.scrollTop + w.clientHeight < w.scrollHeight - 10) {
        w.scrollTop += 600; return true;
    }
}
"""


def _type_and_send(page, label, logger):
    """在已打开的聊天窗口中输入消息并发送"""
    chat_input = page.locator('xpath=//div[contains(@class, "chat-input-")]')
    chat_input.wait_for(timeout=config.get("browserTimeout", 120000))
    message = build_message()
    for line in message.split("\\n"):
        chat_input.type(line)
        if line != message.split("\\n")[-1]:
            chat_input.press("Shift+Enter")
    logger.debug(f"发送: {message[:50]}...")
    chat_input.press("Enter")
    time.sleep(2)
    logger.info(f"已给 {label} 发送续火花消息")
    return True


def _match_target(mapping, target, mode):
    """纯函数：在映射中查找目标。返回 {"nickname": ..., "user_id": ...} 或 None。"""
    if mode == "short_id":
        return mapping.get(str(target))
    for _sid, v in mapping.items():
        if v["nickname"] == str(target):
            return v
    return None


def handle_response_for_map(resp, mapping):
    """拦截 user_detail 响应，更新 short_id 映射"""
    if "user_detail" not in resp.url or resp.status != 200:
        return
    try:
        data = resp.json()
        for item in data.get("user_list", []):
            uid = item.get("user_id", "")
            user = item.get("user", {})
            sid = str(user.get("ShortId", ""))
            nick = user.get("nickname", "")
            if sid and uid:
                mapping[sid] = {"nickname": nick, "user_id": uid}
    except Exception:
        pass


def _dump_visible_names(page, logger):
    """诊断：把列表里实际渲染出的昵称打印到日志（字符用·分隔，避免 GitHub Secret 打码）"""
    try:
        names = page.evaluate("""
        () => {
            var out = [];
            var spans = document.querySelectorAll('[class*="item-header-name-"]');
            for (var i = 0; i < spans.length && i < 30; i++) out.push(spans[i].textContent);
            return out;
        }
        """)
        pretty = " | ".join("·".join(list(str(n))) for n in names)
        logger.info(f"诊断-列表渲染出的昵称(·为分隔): {pretty}")
    except Exception:
        logger.info("诊断-列表渲染出的昵称: (读取失败)")


def _dump_page_state(page, logger):
    """诊断：保存现场截图、页面地址和标题到 logs/ 目录（会随 artifact 上传）"""
    try:
        os.makedirs("logs", exist_ok=True)
        page.screenshot(path="logs/debug_page.png", full_page=True)
        logger.info(f"诊断-截图已保存 logs/debug_page.png")
    except Exception as e:
        logger.error(f"诊断-截图失败: {e}")
    try:
        logger.info(f"诊断-当前页面地址: {page.url}")
        logger.info(f"诊断-页面标题: {page.title()}")
    except Exception as e:
        logger.error(f"诊断-读取页面信息失败: {e}")
    _dump_visible_names(page, logger)


def scroll_and_find(page, mapping, logger):
    """滚动好友列表，拦截 user_detail 收集映射"""
    def on_resp(resp):
        handle_response_for_map(resp, mapping)

    page.on("response", on_resp)

    try:
        page.locator(
            'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]'
        ).click(timeout=5000)
    except Exception:
        pass
    time.sleep(config.get("friendListTimeout", 2000) / 1000)

    for _ in range(80):
        page.evaluate(_SCROLL_CONTAINER_JS)
        time.sleep(0.3)

    page.remove_listener("response", on_resp)
    logger.debug(f"收集到 {len(mapping)} 个 short_id 映射")


def try_click_and_send(page, target, mapping, logger):
    """尝试点击一个目标好友并发送消息。返回 True 如果成功。"""
    info = _match_target(mapping, target, matchMode)

    if not info:
        logger.warning(f"映射中未找到目标: {target}")
        return False

    nickname = info["nickname"]

    # 边滚边找可见元素
    page.evaluate("""
    () => {
        var els = document.querySelectorAll('[class*="semi-list"], #sub-app ul');
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (el) { el.scrollTop = 0; return; }
        }
        var w = document.scrollingElement;
        if (w) w.scrollTop = 0;
    }
    """)
    time.sleep(0.3)

    clicked = False
    for _ in range(80):
        # 检查目标是否可见
        visible = page.evaluate(f"""
        () => {{
            var spans = document.querySelectorAll('[class*="item-header-name-"]');
            for (var i = 0; i < spans.length; i++) {{
                var s = spans[i];
                if (s.textContent === {json.dumps(nickname, ensure_ascii=False)} &&
                    s.offsetParent !== null) {{
                    var r = s.getBoundingClientRect();
                    return r.top >= 0 && r.bottom <= window.innerHeight;
                }}
            }}
            return false;
        }}
        """)
        if visible:
            # 找到了，用坐标点击（绕过 Playwright 可见性检查）
            page.evaluate(f"""
            () => {{
                var spans = document.querySelectorAll('[class*="item-header-name-"]');
                for (var i = 0; i < spans.length; i++) {{
                    if (spans[i].textContent === {json.dumps(nickname, ensure_ascii=False)}) {{
                        spans[i].click();
                        return;
                    }}
                }}
            }}
            """)
            clicked = True
            break

        page.evaluate(_SCROLL_CONTAINER_JS.replace("+= 600", "+= 300"))
        time.sleep(0.2)

    if not clicked:
        _dump_visible_names(page, logger)
        logger.warning(f"未找到可见的 {nickname}")
        return False

    time.sleep(2)
    try:
        return _type_and_send(page, nickname, logger)
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        return False


def do_user_task(signer, username, targets):
    """处理单个账号"""
    page = signer.page
    logger.info(f"开始处理账号 {username}")

    # 收集映射（失败自动刷新重试，最多 4 次）
    mapping = {}
    for attempt in range(4):
        logger.info(f"诊断-第 {attempt + 1} 次尝试, 当前页面地址: {page.url}")
        scroll_and_find(page, mapping, logger)
        if mapping:
            break
        logger.warning(f"第 {attempt + 1} 次未收集到好友信息，刷新页面重试")
        try:
            page.reload(timeout=60000)
            page.wait_for_timeout(10000)
        except Exception as e:
            logger.error(f"刷新页面失败: {e}")

    if not mapping:
        _dump_page_state(page, logger)
        logger.warning(f"未收集到好友信息")
        return

    # 逐个目标发消息
    for target in targets:
        for attempt in range(config.get("taskRetryTimes", 3)):
            try:
                if try_click_and_send(page, target, mapping, logger):
                    break
            except Exception as e:
                logger.warning(f"给 {target} 发消息失败(尝试{attempt+1}): {e}")
                time.sleep(2)

    logger.info(f"账号 {username} 任务完成")


def do_group_task(signer, username, groups):
    """群聊续火花"""
    page = signer.page
    logger.info(f"开始群聊任务: {username}")

    # 重新导航到聊天页（私聊任务后可能跳到了 home）
    page.goto("https://creator.douyin.com/creator-micro/data/following/chat", timeout=30000)
    time.sleep(5)

    # 点击 "群消息" tab
    try:
        tabs = page.evaluate("""
        () => {
            var divs = document.querySelectorAll('#sub-app > div > div > div:first-child > div');
            for (var i = 0; i < divs.length; i++) {
                var d = divs[i]; var r = d.getBoundingClientRect();
                if (d.textContent.indexOf('群消息') >= 0) return {x: r.x + r.width/2, y: r.y + r.height/2};
            }
            return null;
        }
        """)
        if tabs:
            page.mouse.click(tabs['x'], tabs['y'])
            logger.debug("已点击群消息 tab")
            time.sleep(5)
    except Exception as e:
        logger.error(f"点击群消息 tab 失败: {e}")
        return

    # 逐个群发消息
    for group_name in groups:
        group_str = str(group_name)
        for attempt in range(config.get("taskRetryTimes", 3)):
            try:
                sent = _send_to_group(page, group_str, logger)
                if sent:
                    break
            except Exception as e:
                logger.warning(f"群 {group_str} 发消息失败(尝试{attempt+1}): {e}")
                time.sleep(2)

    logger.info(f"群聊任务完成: {username}")


def _send_to_group(page, target, logger):
    """在群列表中找到目标群，点击并发送消息"""
    # 滚回顶部
    page.evaluate("""
    () => {
        var els = document.querySelectorAll('[class*="semi-list"], #sub-app ul');
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (el) { el.scrollTop = 0; return; }
        }
        var w = document.scrollingElement;
        if (w) w.scrollTop = 0;
    }
    """)
    time.sleep(0.3)

    clicked = False
    for _ in range(80):
        # 找目标群（名称包含匹配）
        found = page.evaluate("""
        (target) => {
            var spans = document.querySelectorAll('[class*="item-header-name-"]');
            for (var i = 0; i < spans.length; i++) {
                var s = spans[i];
                if (s.textContent.indexOf(target) >= 0 &&
                    s.offsetParent !== null) {
                    var r = s.getBoundingClientRect();
                    if (r.top >= 0 && r.bottom <= window.innerHeight) {
                        s.click();
                        return s.textContent;
                    }
                }
            }
            return null;
        }
        """, target)

        if found:
            clicked = True
            break

        page.evaluate(_SCROLL_CONTAINER_JS.replace("+= 600", "+= 300"))
        time.sleep(0.2)

    if not clicked:
        logger.warning(f"未找到群聊: {target}")
        return False

    time.sleep(2)
    try:
        return _type_and_send(page, target, logger)
    except Exception as e:
        logger.error(f"群聊发送消息失败: {e}")
        return False


def runTasks():
    logger.info("开始执行任务")
    for user in userData:
        username = user.get("username", "未知用户")
        cookies = user["cookies"]
        targets = user.get("targets", [])
        groups = user.get("groups", [])

        if not targets and not groups:
            logger.warning(f"账号 {username} 没有目标好友或群聊")
            continue

        signer = None
        try:
            signer = Signer(cookies)
            if targets:
                do_user_task(signer, username, targets)
            if groups:
                do_group_task(signer, username, groups)
        except Exception as e:
            logger.error(f"账号 {username} 处理失败: {e}")
            traceback.print_exc()
        finally:
            if signer:
                signer.close()
    logger.info("所有任务执行完毕")
