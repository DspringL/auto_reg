# coding=utf-8
"""
使用 Playwright 通过神龙代理（API 提取模式）打开浏览器，
访问 IP 查询网站，直观验证出口 IP 是否为干净的美国住宅 IP

依赖安装：
  pip install playwright requests
  playwright install chromium
"""

import asyncio
import re
from playwright.async_api import async_playwright
from shenlong_proxy import fetch_proxy_list, pick_proxy, PROXY_PROTOCOL


# 要访问的 IP 查询页面
IP_CHECK_URLS = [
    "https://www.ip138.com/",
    "https://whatismyipaddress.com/",
    "https://browserleaks.com/ip",
]


async def run(headless: bool = False, fetch_count: int = 1):
    """
    主流程：
      1. 调用 API 提取指定数量的代理
      2. 随机选一个代理
      3. 用 Playwright 打开浏览器，通过该代理访问 IP 查询页面

    Args:
        headless:     是否无头模式
        fetch_count:  API 提取代理数量（1=每次重新提取，>1=提前批量提取备用）
    """
    # Step 1: 提取代理列表
    print("=" * 55)
    print(f"【Step 1】从神龙 API 提取 {fetch_count} 个代理...")
    proxy_lines = fetch_proxy_list(count=fetch_count)
    if len(proxy_lines) <= 5:
        print(f"提取结果：{proxy_lines}")
    else:
        print(f"共提取 {len(proxy_lines)} 个代理，前 5 个：")
        for line in proxy_lines[:5]:
            print(f"  {line}")

    # Step 2: 随机选一个（count=1 时直接用唯一的那个）
    proxy = pick_proxy(proxy_lines, PROXY_PROTOCOL)
    print(f"\n【Step 2】已选取代理：{proxy.to_url()}")

    # Step 3: 启动 Playwright
    print(f"\n【Step 3】启动 Playwright 浏览器（headless={headless}）...")
    print("=" * 55)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        context = await browser.new_context(
            proxy=proxy.to_playwright_proxy(),
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )

        page = await context.new_page()

        for url in IP_CHECK_URLS:
            print(f"\n正在访问：{url}")
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                # 等待页面内容渲染
                await page.wait_for_timeout(3000)

                title = await page.title()
                print(f"页面标题：{title}")

                # 尝试从页面提取 IP
                ip = await extract_ip(page)
                if ip:
                    print(f"检测到出口 IP：{ip}")
                else:
                    print("未能自动提取 IP，请查看浏览器窗口")

                # 截图保存
                shot_path = f"/tmp/shenlong_{url.split('/')[2]}.png"
                await page.screenshot(path=shot_path)
                print(f"截图已保存：{shot_path}")

                # 非 headless 模式暂停，让用户肉眼查看
                if not headless:
                    print("\n请在浏览器中查看页面显示的 IP，确认为美国出口 IP。")
                    print("按 Enter 访问下一个网址，Ctrl+C 退出...")
                    await asyncio.get_event_loop().run_in_executor(None, input)

            except Exception as e:
                print(f"访问失败：{e}")
                # 代理不可用时重新从 API 提取一个新代理
                print("代理不可用，重新从 API 提取...")
                new_lines = fetch_proxy_list(count=1)
                if not new_lines:
                    print("无法提取新代理，终止")
                    break
                proxy = pick_proxy(new_lines, PROXY_PROTOCOL)
                print(f"新代理：{proxy.to_url()}")
                # 重建 context 使用新代理
                await context.close()
                context = await browser.new_context(
                    proxy=proxy.to_playwright_proxy(),
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                page = await context.new_page()
                continue

        await browser.close()
        print("\n浏览器已关闭，验证完成。")


async def extract_ip(page) -> str:
    """从页面内容中用正则提取 IPv4 地址"""
    try:
        content = await page.content()
        matches = re.findall(r'\b(\d{1,3}\.){3}\d{1,3}\b', content)
        # 过滤掉局域网 IP 和 0.0.0.0
        public_ips = [
            ip for ip in matches
            if not ip.startswith(("192.168.", "10.", "172.", "127.", "0."))
        ]
        return public_ips[0] if public_ips else ""
    except Exception:
        return ""


if __name__ == "__main__":
    asyncio.run(run(headless=False, fetch_count=1))
