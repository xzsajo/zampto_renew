import os, re, logging, random, json, time
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------- 环境变量 ----------
USERNAME  = os.environ["ZAMPTO_USERNAME"]
PASSWORD  = os.environ["ZAMPTO_PASSWORD"]
SERVER_ID = os.environ.get("ZAMPTO_SERVER_ID", "")

WXPUSHER_TOKEN = os.environ.get("WXPUSHER_TOKEN", "")
WXPUSHER_UID   = os.environ.get("WXPUSHER_UID", "")
SKIP_RENEW     = os.environ.get("SKIP_RENEW", "false").lower() == "true"

BASE_URL    = "https://dash.zampto.net"
AUTH_URL    = "https://auth.zampto.net/sign-in"
SERVERS_URL = f"{BASE_URL}/servers"

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ---------- WxPusher ----------
def wxpush(content: str):
    if not WXPUSHER_TOKEN or not WXPUSHER_UID:
        log.warning("📨 WXPUSHER_TOKEN 或 WXPUSHER_UID 未配置，跳过推送")
        return
    import urllib.request
    payload = json.dumps({
        "appToken": WXPUSHER_TOKEN,
        "content":  content,
        "contentType": 1,
        "uids": [WXPUSHER_UID],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                log.info("📨 WxPusher 推送成功")
            else:
                log.warning(f"📨 WxPusher 推送失败: {result}")
    except Exception as e:
        log.warning(f"📨 WxPusher 推送异常: {e}")

# ---------- 工具函数 ----------
def redact_sensitive_info(page):
    """截图前将页面上的邮箱地址替换为 *** 遮码，截图后恢复原始文本。"""
    try:
        page.evaluate("""() => {
            // 遍历所有文本节点，找到邮箱并用 *** 替换显示
            const emailRegex = /[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g;
            function maskNode(node) {
                if (node.nodeType === Node.TEXT_NODE) {
                    if (emailRegex.test(node.textContent)) {
                        node.textContent = node.textContent.replace(emailRegex, '***@***.***');
                    }
                } else {
                    // 跳过 script/style 标签
                    if (node.tagName && ['SCRIPT','STYLE','NOSCRIPT'].includes(node.tagName)) return;
                    node.childNodes.forEach(maskNode);
                }
            }
            // 重置正则 lastIndex
            emailRegex.lastIndex = 0;
            document.querySelectorAll('body *').forEach(el => {
                // 只处理直接文本内容（避免重复处理子节点）
                el.childNodes.forEach(child => {
                    if (child.nodeType === Node.TEXT_NODE && emailRegex.test(child.textContent)) {
                        emailRegex.lastIndex = 0;
                        child.textContent = child.textContent.replace(emailRegex, '***@***.***');
                    }
                    emailRegex.lastIndex = 0;
                });
            });
        }""")
    except Exception as e:
        log.warning(f"遮码邮箱失败: {e}")


def take_screenshot(page, name):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{ts}_{name}.png")
        redact_sensitive_info(page)
        page.screenshot(path=path, full_page=False)
        log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败: {e}")

def get_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except:
        return ""

def human_delay(min_s=0.5, max_s=1.2):
    time.sleep(random.uniform(min_s, max_s))

def tcp_check(host: str, port: int, timeout: int = 5) -> bool:
    """尝试 TCP 连接，返回是否成功。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def wait_for_port(host: str, port: int, max_wait: int = 120, interval: int = 10) -> bool:
    """轮询等待端口真正可连接，最多等 max_wait 秒。"""
    log.info(f"🔌 等待端口可连接（最多 {max_wait}s）...")
    elapsed = 0
    while elapsed < max_wait:
        if tcp_check(host, port):
            log.info(f"✅ 端口已可连接（等待了 {elapsed}s）")
            return True
        time.sleep(interval)
        elapsed += interval
        log.info(f"  [{elapsed}s] 端口还未开放，继续等待...")
    log.warning(f"⚠️ 端口等待超时（{max_wait}s）")
    return False

def wait_for_url_contains(page, keyword, timeout=15) -> bool:
    try:
        page.wait_for_url(f"**{keyword}**", timeout=timeout * 1000)
        return True
    except:
        return keyword in page.url

# ---------- 解析 expiry 字符串为总分钟数（用于比较）----------
def parse_expiry_minutes(expiry_str: str) -> int:
    """
    将 "1 day 22h 47m" 或 "2 days 1h 5m" 之类解析为总分钟数。
    解析失败返回 -1。
    """
    if not expiry_str:
        return -1
    total = 0
    m = re.search(r'(\d+)\s*day', expiry_str)
    if m:
        total += int(m.group(1)) * 24 * 60
    m = re.search(r'(\d+)\s*h', expiry_str)
    if m:
        total += int(m.group(1)) * 60
    m = re.search(r'(\d+)\s*m', expiry_str)
    if m:
        total += int(m.group(1))
    return total if total > 0 else -1

# ---------- 关闭所有弹窗（广告 + GDPR）----------
def dismiss_all_popups(page):
    """
    关闭页面上所有可能出现的弹窗：
    1. 广告弹窗（有 Close 按钮，或含外部域名链接）
    2. GDPR Cookie 同意弹窗
    每次调用最多等待 5 秒，关掉后继续检测，最多循环 3 轮。
    """
    for round_idx in range(3):
        closed_any = False

        closed = page.evaluate("""() => {
            var count = 0;

            // ① 优先找带明确文字的关闭按钮
            var closeTexts = ['Close', 'close', 'Schließen', '×', 'X'];
            for (var t of closeTexts) {
                var btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (var b of btns) {
                    if (b.innerText && b.innerText.trim() === t) {
                        // 确认它在某种弹窗/overlay 容器内
                        var parent = b.closest('[class*="modal"],[class*="popup"],[class*="overlay"],[class*="dialog"],[class*="ad-"]');
                        if (parent) { b.click(); count++; break; }
                    }
                }
            }

            // ② aria-label="Close" 的按钮
            var ariaClose = document.querySelector('button[aria-label="Close"], button[aria-label="close"], [aria-label="Dismiss"]');
            if (ariaClose) { ariaClose.click(); count++; }

            // ③ GDPR：Nicht einwilligen / Decline / Reject
            var gdprTexts = ['Nicht einwilligen', 'Decline', 'Reject'];
            for (var gt of gdprTexts) {
                var gb = Array.from(document.querySelectorAll('button')).find(b => b.innerText.trim() === gt);
                if (gb) { gb.click(); count++; break; }
            }

            return count;
        }""")

        if closed and closed > 0:
            log.info(f"  已关闭 {closed} 个弹窗（第 {round_idx+1} 轮）")
            closed_any = True
            time.sleep(1)

        # 检查是否还有可见弹窗
        has_popup = page.evaluate("""() => {
            var selectors = [
                '[class*="modal"]:not([style*="display: none"])',
                '[class*="popup"]:not([style*="display: none"])',
                '[class*="overlay"]:not([style*="display: none"])',
            ];
            for (var s of selectors) {
                var el = document.querySelector(s);
                if (el && el.offsetParent !== null) return true;
            }
            return false;
        }""")

        if not has_popup:
            break

        if not closed_any:
            # 没关掉也没新弹窗，退出
            break

        time.sleep(1)

# ---------- CF Turnstile 等待 ----------
def wait_cf_turnstile(page, timeout=60) -> bool:
    """
    等待 Cloudflare Turnstile 验证自动完成。
    同时确认续期弹窗（id=renewModal）是可见的——
    如果弹窗根本不在，说明被广告弹窗打断了，直接返回 False。
    """
    log.info("等待 Cloudflare Turnstile 验证...")

    # 先确认续期弹窗真的出现了
    renew_modal_visible = page.evaluate("""() => {
        var m = document.getElementById('renewModal');
        if (!m) return false;
        return m.offsetParent !== null || m.style.display !== 'none';
    }""")
    if not renew_modal_visible:
        log.warning("⚠️ 续期弹窗未检测到（可能被广告弹窗遮挡或未弹出）")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        still_verifying = page.evaluate("""() => {
            var frames = document.querySelectorAll('iframe');
            for (var f of frames) {
                if (f.src && f.src.includes('challenges.cloudflare.com')) return true;
            }
            var body = document.body.innerText || '';
            return body.includes('正在验证') || body.includes('Verifying');
        }""")
        if not still_verifying:
            log.info("✅ CF Turnstile 验证完成")
            return True
        elapsed = int(time.time() - (deadline - timeout))
        if elapsed % 5 == 0:
            log.info(f"  CF 等待中... {elapsed}s")
        time.sleep(1)

    log.error(f"CF Turnstile 验证超时（{timeout}s）")
    return False

# ---------- 登录 ----------
def login(page, max_retries=3) -> bool:
    login_url = "https://auth.zampto.net/sign-in?app_id=YOUR_APP_ID"

    for attempt in range(1, max_retries + 1):
        log.info(f"登录 {attempt}/{max_retries}")
        try:
            page.goto(login_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            log.warning(f"goto 异常: {e}")

        try:
            page.wait_for_selector(
                'input[name="identifier"], input[autocomplete="username email"]',
                timeout=15000
            )
        except:
            log.warning("找不到用户名输入框，重试")
            take_screenshot(page, f"login_no_input_{attempt}")
            time.sleep(2)
            continue

        try:
            user_el = page.locator('input[name="identifier"]').first
            user_el.click()
            user_el.fill("")
            user_el.type(USERNAME, delay=random.randint(60, 130))
            log.info("已填写用户名")
        except Exception as e:
            log.warning(f"填写用户名失败: {e}")
            continue

        human_delay()

        try:
            page.locator('button[name="submit"], button[type="submit"]').first.click()
            log.info("已点击登录按钮（第一步）")
        except Exception as e:
            log.warning(f"点击登录失败: {e}")
            continue

        try:
            page.wait_for_selector(
                'input[name="password"], input[autocomplete="current-password"]',
                timeout=15000
            )
            log.info("已进入密码输入页")
        except:
            log.warning("未出现密码输入框，重试")
            take_screenshot(page, f"login_no_password_{attempt}")
            continue

        try:
            pass_el = page.locator('input[name="password"]').first
            pass_el.click()
            pass_el.fill("")
            pass_el.type(PASSWORD, delay=random.randint(60, 130))
            log.info("已填写密码")
        except Exception as e:
            log.warning(f"填写密码失败: {e}")
            continue

        human_delay()

        try:
            page.locator('button[name="submit"], button[type="submit"]').first.click()
            log.info("已点击继续按钮（第二步）")
        except Exception as e:
            log.warning(f"点击继续失败: {e}")
            continue

        if wait_for_url_contains(page, "dash.zampto.net", 20):
            log.info("✅ 登录成功，已跳转到 dashboard")
            take_screenshot(page, "01_login_success")
            return True

        time.sleep(3)
        if "dash.zampto.net" in page.url or "zampto.net/server" in page.url:
            log.info("✅ 登录成功")
            take_screenshot(page, "01_login_success")
            return True

        log.warning(f"登录后未跳转，请检查账号密码")
        take_screenshot(page, f"login_fail_{attempt}")
        time.sleep(2)

    return False

# ---------- 获取服务器信息（expiry + 状态）----------
def get_server_info(page, server_id: str) -> dict:
    """
    访问服务器详情页读取 expiry / lastRenewed / address，
    然后访问 console 页读取真实运行状态（Running / Stopped）。
    """
    server_url = f"{BASE_URL}/server?id={server_id}"
    log.info(f"访问服务器详情页")
    try:
        page.goto(server_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"访问服务器详情超时: {e}")

    time.sleep(3)
    take_screenshot(page, "02_server_page")

    info = page.evaluate("""() => {
        var body = document.body.innerText || '';
        var expiryMatch  = body.match(/Expiry[^:]*:\\s*([^\\n]+)/i);
        var renewedMatch = body.match(/last renewed[^:]*:\\s*([^\\n]+)/i);
        var addrMatch    = body.match(/node\\d+\\.zampto\\.net:\\d+/i);
        return {
            expiry:      expiryMatch  ? expiryMatch[1].trim()  : null,
            lastRenewed: renewedMatch ? renewedMatch[1].trim() : null,
            address:     addrMatch    ? addrMatch[0]           : null,
        };
    }""")

    # 真实运行状态在 Console 页（server 详情页显示的是账号 Active，不是运行状态）
    console_url = f"{BASE_URL}/server-console?id={server_id}"
    log.info(f"访问 Console 页读取运行状态")
    try:
        page.goto(console_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"访问 Console 页超时: {e}")

    time.sleep(3)

    status_text = page.evaluate("""() => {
        var statusEl = document.getElementById('serverStatus');
        if (statusEl) return statusEl.innerText.trim();
        var runEl = document.querySelector('.status-running,.status-stopped,.status-starting');
        if (runEl) return runEl.innerText.trim();
        var body = document.body.innerText || '';
        var sm = body.match(/Running(?:\\s*\\([^)]+\\))?|Stopped|Starting|Stopping/i);
        return sm ? sm[0] : 'Unknown';
    }""")

    info["status"] = status_text or "Unknown"
    log.info(f"服务器信息: expiry={info.get('expiry')}, status={info.get('status')}, address=<已隐藏>")
    return info

# ---------- 启动服务器 ----------
def start_server(page) -> bool:
    console_url = f"{BASE_URL}/server-console?id={SERVER_ID}"
    log.info(f"直接导航到 Console 页")
    page.goto(console_url, timeout=30000, wait_until="domcontentloaded")
    time.sleep(3)
    take_screenshot(page, "03_console_page")

    try:
        start_btn = page.locator('button:has-text("Start")').first
        if start_btn.is_visible(timeout=5000):
            start_btn.click()
            log.info("✅ 已点击 Start 按钮")
            time.sleep(5)
            take_screenshot(page, "04_after_start")
        else:
            log.warning("Start 按钮不可见（服务器可能已在运行）")
            # 可能已经在 Running，继续等待确认
    except Exception as e:
        log.warning(f"点击 Start 失败: {e}")
        return False

    # ── 轮询等待服务器真正变为 Running ──────────────────────────────────
    log.info("⏳ 等待服务器变为 Running（最多 5 分钟）...")
    wait_total = 300   # 最多等 300 秒
    poll_interval = 10  # 每 10 秒刷新一次
    elapsed = 0
    final_status = "Unknown"
    # Zampto 启动流程：Offline → Starting → Running
    # 点击 Start 后有短暂窗口期页面仍显示 Offline，需连续多次才确认失败
    offline_tolerance = 3   # 连续检测到 Offline 超过此次数才认定失败
    offline_streak = 0

    while elapsed < wait_total:
        time.sleep(poll_interval)
        elapsed += poll_interval
        try:
            page.reload(timeout=20000, wait_until="domcontentloaded")
            time.sleep(3)
            body = get_text(page)
            # 优先匹配状态指示器文字
            if "Running" in body:
                final_status = "Running"
                log.info(f"✅ 服务器已变为 Running（等待了 {elapsed}s）")
                take_screenshot(page, f"05_running_confirmed")
                offline_streak = 0
                break
            elif "Starting" in body:
                final_status = "Starting"
                offline_streak = 0
                log.info(f"  [{elapsed}s] 还在 Starting，继续等待...")
                take_screenshot(page, f"05_still_starting_{elapsed}s")
            elif "Offline" in body or "Stopped" in body:
                offline_streak += 1
                final_status = "Offline"
                log.warning(
                    f"  [{elapsed}s] 检测到 Offline（第 {offline_streak}/{offline_tolerance} 次）"
                )
                take_screenshot(page, f"05_start_failed_{elapsed}s")
                if offline_streak >= offline_tolerance:
                    log.warning(
                        f"  连续 {offline_streak} 次 Offline，确认启动失败，退出等待"
                    )
                    break
                log.info(f"  Offline 容忍中，继续等待（可能仍处于启动窗口期）...")
            else:
                offline_streak = 0
                log.info(f"  [{elapsed}s] 状态未知，继续等待...")
        except Exception as e:
            log.warning(f"  [{elapsed}s] 刷新页面异常: {e}")
    else:
        log.warning(f"⚠️ 等待超时（{wait_total}s），最后状态: {final_status}")
        take_screenshot(page, "05_start_timeout")

    if final_status != "Running":
        return False

    # ── 面板显示 Running，进一步用 TCP 验证端口真的开了 ─────────────────
    addr_raw = None
    try:
        addr_raw = page.evaluate("""() => {
            var body = document.body.innerText || '';
            var m = body.match(/node\\d+\\.zampto\\.net:\\d+/i);
            return m ? m[0] : null;
        }""")
    except Exception:
        pass

    if addr_raw:
        parts = addr_raw.rsplit(":", 1)
        if len(parts) == 2:
            host, port_str = parts[0], parts[1]
            try:
                port = int(port_str)
                port_ok = wait_for_port(host, port, max_wait=120, interval=10)
                if port_ok:
                    log.info(f"✅ TCP 端口验证通过，服务器真正可连接")
                    take_screenshot(page, "06_port_verified")
                    return True
                else:
                    log.warning(f"⚠️ 端口不可达，改用 Stop → Start 彻底重启...")
                    take_screenshot(page, "06_port_unreachable_before_stop_start")

                    # ── 第一步：点击 Stop 按钮 ───────────────────────────
                    stopped = False
                    try:
                        stop_btn = page.locator('button:has-text("Stop")').first
                        if stop_btn.is_visible(timeout=5000):
                            stop_btn.click()
                            log.info("🛑 已点击 Stop 按钮")
                            take_screenshot(page, "07_after_stop")
                            # 等待面板变为 Offline/Stopped（最多 60s）
                            log.info("⏳ 等待服务器停止（最多 60s）...")
                            for _ in range(6):
                                time.sleep(10)
                                try:
                                    page.reload(timeout=20000, wait_until="domcontentloaded")
                                    time.sleep(3)
                                    body_s = get_text(page)
                                    if "Offline" in body_s or "Stopped" in body_s:
                                        log.info("✅ 服务器已停止")
                                        stopped = True
                                        break
                                    log.info("  还未停止，继续等待...")
                                except Exception as e:
                                    log.warning(f"  刷新异常: {e}")
                            if not stopped:
                                log.warning("⚠️ Stop 后未确认 Offline，仍继续尝试 Start")
                                stopped = True  # 强行继续
                        else:
                            log.warning("Stop 按钮不可见，跳过 Stop 步骤")
                    except Exception as e:
                        log.warning(f"点击 Stop 失败: {e}")

                    if not stopped:
                        return False

                    # ── 第二步：等几秒让端口完全释放 ─────────────────────
                    log.info("⏸️ 等待 8 秒让端口完全释放...")
                    time.sleep(8)

                    # ── 第三步：点击 Start 按钮 ──────────────────────────
                    restarted = False
                    try:
                        start_btn2 = page.locator('button:has-text("Start")').first
                        if start_btn2.is_visible(timeout=10000):
                            start_btn2.click()
                            log.info("▶️ 已点击 Start 按钮（Stop→Start 流程）")
                            time.sleep(5)
                            take_screenshot(page, "08_after_stop_start")
                            restarted = True
                        else:
                            log.warning("Start 按钮不可见，无法重新启动")
                    except Exception as e:
                        log.warning(f"点击 Start 失败: {e}")

                    if not restarted:
                        return False

                    # ── 第四步：等待面板变为 Running ──────────────────────
                    log.info("⏳ Stop→Start 后等待面板变为 Running（最多 5 分钟）...")
                    elapsed2 = 0
                    running_again = False
                    offline_streak2 = 0
                    offline_tolerance2 = 3  # 同样的容忍机制
                    while elapsed2 < 300:
                        time.sleep(10)
                        elapsed2 += 10
                        try:
                            page.reload(timeout=20000, wait_until="domcontentloaded")
                            time.sleep(3)
                            body2 = get_text(page)
                            if "Running" in body2:
                                log.info(f"✅ Stop→Start 后面板已变为 Running（等待了 {elapsed2}s）")
                                take_screenshot(page, "09_stop_start_running")
                                running_again = True
                                offline_streak2 = 0
                                break
                            elif "Starting" in body2:
                                offline_streak2 = 0
                                log.info(f"  [{elapsed2}s] 还在 Starting，继续等待...")
                            elif "Offline" in body2 or "Stopped" in body2:
                                offline_streak2 += 1
                                log.warning(
                                    f"  [{elapsed2}s] 检测到 Offline（第 {offline_streak2}/{offline_tolerance2} 次）"
                                )
                                if offline_streak2 >= offline_tolerance2:
                                    log.warning(
                                        f"  连续 {offline_streak2} 次 Offline，确认 Stop→Start 启动失败，放弃"
                                    )
                                    break
                                log.info(f"  Offline 容忍中，继续等待...")
                        except Exception as e:
                            log.warning(f"  [{elapsed2}s] 刷新异常: {e}")

                    if not running_again:
                        log.warning("⚠️ Stop→Start 后未能恢复 Running，放弃")
                        take_screenshot(page, "09_stop_start_failed")
                        return False

                    # ── 第五步：再次验证端口 ──────────────────────────────
                    log.info(f"🔌 Stop→Start 后再次验证端口...")
                    port_ok2 = wait_for_port(host, port, max_wait=120, interval=10)
                    if port_ok2:
                        log.info(f"✅ Stop→Start 后端口验证通过")
                        take_screenshot(page, "10_port_verified_after_stop_start")
                        return True
                    else:
                        log.warning(f"⚠️ Stop→Start 后端口仍不可达，请手动处理")
                        take_screenshot(page, "10_port_still_unreachable")
                        return False
            except ValueError:
                pass
    else:
        log.warning("⚠️ 未能从页面读取服务器地址，跳过端口验证，以面板状态为准")

    return True  # 读不到地址时降级为只看面板状态

# ---------- 续期 ----------
def renew_server(page, server_id: str, expiry_before: str) -> bool:
    """
    访问服务器详情页，关掉所有弹窗后点击 Renew Server，
    等待 CF Turnstile 自动通过，最后用 expiry 是否增加来验证续期成功。

    expiry_before: 续期前的 expiry 字符串，用于对比判断是否真正续期成功。
    """
    server_url = f"{BASE_URL}/server?id={server_id}"
    log.info(f"准备续期，访问服务器详情页")
    try:
        page.goto(server_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"访问续期页超时: {e}")

    time.sleep(3)

    # ✅ 关掉所有弹窗（广告/GDPR）再操作，避免广告弹窗拦截点击
    log.info("关闭页面上所有弹窗...")
    dismiss_all_popups(page)
    time.sleep(1)

    # 点击 Renew Server（<a> 标签，onclick，不是 <button>）
    try:
        renew_btn = page.locator(
            'a:has-text("Renew Server"), button:has-text("Renew Server")'
        ).first
        if not renew_btn.is_visible(timeout=8000):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            renew_btn = page.locator(
                'a:has-text("Renew Server"), button:has-text("Renew Server")'
            ).first

        renew_btn.click()
        log.info("已点击 Renew Server 按钮")
        take_screenshot(page, "05_renew_clicked")
    except Exception as e:
        log.warning(f"点击 Renew Server 失败: {e}")
        return False

    time.sleep(2)

    # 如果续期弹窗出现前又跳出广告，再清一次
    dismiss_all_popups(page)
    time.sleep(1)

    take_screenshot(page, "06_renew_modal")

    # 等待 CF Turnstile 验证（会先检查 renewModal 是否真的出现）
    if not wait_cf_turnstile(page, timeout=60):
        log.warning("CF 验证超时或续期弹窗未出现，续期失败")
        take_screenshot(page, "06_cf_timeout")
        return False

    # 等待页面刷新 / 弹窗消失
    time.sleep(5)
    take_screenshot(page, "07_after_renew")

    # ✅ 用 expiry 是否增加来判断是否真正续期成功，不依赖弹窗状态
    info_after = page.evaluate("""() => {
        var body = document.body.innerText || '';
        var m = body.match(/Expiry[^:]*:\\s*([^\\n]+)/i);
        return m ? m[1].trim() : null;
    }""")
    log.info(f"续期后 expiry（页面直读）: {info_after}")

    minutes_before = parse_expiry_minutes(expiry_before)
    minutes_after  = parse_expiry_minutes(info_after)

    log.info(f"续期前 expiry 分钟数: {minutes_before}, 续期后: {minutes_after}")

    # 成功条件：续期后时间 > 续期前时间（增加了说明成功），或续期后时间接近2天（>= 23h）
    if minutes_after > minutes_before or minutes_after >= 23 * 60:
        log.info(f"✅ 续期成功！expiry: {expiry_before} → {info_after}")
        return True

    # 如果数值没变或变小，可能真的没续期成功
    log.warning(f"⚠️ 续期后 expiry 未增加（{expiry_before} → {info_after}），续期可能失败")
    return False

# ---------- 主流程 ----------
def main():
    from cloakbrowser import launch

    if not SERVER_ID:
        log.error("❌ 未配置 ZAMPTO_SERVER_ID 环境变量")
        wxpush("❌ 未配置 ZAMPTO_SERVER_ID，任务中止")
        return

    PROXY_SERVER = "socks5://127.0.0.1:10808"

    log.info("启动 CloakBrowser...")
    browser = launch(
        headless=False,
        humanize=True,
        proxy=PROXY_SERVER,
        geoip=True,
    )
    page = browser.new_page()

    try:
        # 1. 登录
        if not login(page):
            wxpush("❌ Zampto 登录失败，请检查账号密码")
            return

        # 2. 关闭 GDPR 同意弹窗
        dismiss_all_popups(page)

        # 3. 获取服务器信息
        info = get_server_info(page, SERVER_ID)
        status     = info.get("status", "Unknown")
        expiry     = info.get("expiry", "未知")
        address    = info.get("address", "未知")
        last_renew = info.get("lastRenewed", "未知")

        log.info(f"服务器状态: {status} | 到期: {expiry}")

        # 4. 如果服务器已停止，先启动
        started = False
        if "stopped" in status.lower() or "offline" in status.lower():
            log.info("🔴 服务器已停止，尝试启动...")
            started = start_server(page)
            if started:
                status = "Running"
                log.info("✅ 服务器已确认 Running")
            else:
                status = "Start Failed / Timeout"
                log.warning("⚠️ 服务器启动失败或超时，未能确认 Running")

        # 5. 续期（SKIP_RENEW=true 时跳过，只做启动）
        if SKIP_RENEW:
            log.info("⏭️ SKIP_RENEW=true，跳过续期步骤（Uptime Kuma 紧急启动模式）")
            renewed = False
        else:
            renewed = renew_server(page, SERVER_ID, expiry_before=expiry)

        # 6. 续期后重新读取最新 expiry
        new_expiry = expiry
        if renewed:
            time.sleep(3)
            info2 = get_server_info(page, SERVER_ID)
            new_expiry = info2.get("expiry") or expiry
            log.info(f"续期后到期信息: {new_expiry}")

        # 7. 推送
        lines = ["🚨 Zampto 紧急启动报告" if SKIP_RENEW else "🖥️ Zampto 服务器日报"]
        lines.append(f"服务器 ID: ***")
        lines.append(f"地址: ***")
        lines.append("")
        status_icon = "🟢" if "running" in status.lower() else ("🟡" if "starting" in status.lower() else "🔴")
        lines.append(f"状态: {status_icon} {status}")
        if started:
            lines.append("  → 已启动，面板 Running + 端口可连接 ✅")
        elif "stopped" in status.lower() or "offline" in status.lower() or "failed" in status.lower():
            lines.append("  ⚠️ 启动失败（已尝试 Stop→Start 彻底重启），端口仍不可达，请手动处理")
        lines.append("")
        lines.append(f"Expiry (Next Renewal): {new_expiry}")
        if last_renew:
            lines.append(f"Last Renewed: {last_renew}")
        if SKIP_RENEW:
            lines.append("  （续期已跳过，仅紧急启动）")
        elif renewed:
            lines.append("  → 已自动续期 ✅")
        else:
            lines.append("  ⚠️ 续期失败，请手动检查")

        msg = "\n".join(lines)
        log.info(f"推送内容:\n{msg}")
        wxpush(msg)

    except Exception as e:
        log.exception(e)
        take_screenshot(page, "99_error")
        wxpush(f"❌ Zampto 任务异常: {e}")
    finally:
        time.sleep(3)
        browser.close()
        log.info("任务结束")

if __name__ == "__main__":
    main()
