# 神龙海外代理工具

基于神龙代理 API 提取模式，通过 Playwright 浏览器验证出口 IP 的工具套件。

## 前置条件

- macOS 系统，已安装 Python 3.10+
- 本机已开启**境外全局网络**（如 Clash Verge 全局代理，默认端口 `7897`）
- 拥有神龙代理账号及有效套餐

> 神龙代理属于高质量海外住宅 IP 服务，**必须在已有境外网络的基础上才能使用**。
> Clash Verge 负责让本机联通境外，神龙代理负责提供更纯净、不被封禁的住宅 IP。

---

## 文件结构

```
IP/
├── shenlong_proxy.py   # 核心工具函数（API 提取、代理选取、requests 验证）
├── verify_proxy.py     # Playwright 浏览器验证脚本（主入口）
├── run.sh              # 一键启动脚本（自动创建虚拟环境）
├── demo.py             # 神龙官方原版 demo（Python 2，仅参考）
└── README.md           # 本文档
```

---

## 快速开始

```bash
cd /Users/admin/Downloads/IP
./run.sh
```

`run.sh` 会自动完成以下步骤，无需手动操作：

1. 创建继承全局依赖的虚拟环境（`.venv/`）
2. 检查并补装缺失依赖
3. 检查 Playwright Chromium 是否已安装
4. 运行 `verify_proxy.py`

---

## 工作原理

```
Clash Verge (境外网络)
       │
       ▼
神龙 API 接口  ──提取500个美国IP──▶  随机选1个 IP:PORT
                                           │
                                           ▼
                               Playwright Chromium
                                           │
                                           ▼
                               ip138.com / whatismyipaddress.com
                               （验证出口 IP 是否为美国住宅 IP）
```

---

## 配置说明

所有配置集中在 `shenlong_proxy.py` 顶部：

```python
# 神龙代理 API 提取链接
API_URL = "http://api.shenlongproxy.com/ip?cty=US&c=500&pt=1&ft=txt&pat=\\n&rep=1&key=7a7e4c02&ts=30"

# 代理协议：http（pt=1）或 socks5（pt=3）
PROXY_PROTOCOL = "http"

# 本机 Clash Verge 监听端口
CLASH_PROXY = "http://127.0.0.1:7897"
```

### API 链接参数说明

| 参数 | 当前值 | 含义 |
|------|--------|------|
| `cty` | `US` | 提取美国 IP，可改为 `GB`、`JP` 等 |
| `c` | `500` | 单次提取数量（最大值取决于套餐） |
| `pt` | `1` | 协议类型：`1`=HTTP，`3`=SOCKS5 |
| `ft` | `txt` | 返回格式：纯文本，每行一个 `IP:PORT` |
| `rep` | `1` | 去重（1=开启） |
| `key` | `7a7e4c02` | API 密钥，勿泄露 |
| `ts` | `30` | IP 有效期（分钟） |

### 切换 SOCKS5 协议

将 `shenlong_proxy.py` 中两处同步修改：

```python
API_URL = "...&pt=3&..."   # pt 改为 3
PROXY_PROTOCOL = "socks5"
```

同时需要安装 SOCKS5 支持：

```bash
pip install requests[socks]
```

---

## 核心模块

### `shenlong_proxy.py`

#### `ProxyConfig`

代理配置数据类，封装 host / port / protocol，提供三种格式转换：

```python
proxy = ProxyConfig(host="1.2.3.4", port=8080, protocol="http")

proxy.to_url()                # "http://1.2.3.4:8080"
proxy.to_requests_proxies()   # {"http": "http://1.2.3.4:8080", "https": "..."}
proxy.to_playwright_proxy()   # {"server": "http://1.2.3.4:8080"}
```

#### `fetch_proxy_list(api_url)`

调用神龙 API，返回 `["IP:PORT", ...]` 列表。通过 Clash 代理发起请求。

#### `pick_proxy(proxy_lines, protocol)`

从列表中随机选一个，返回 `ProxyConfig` 对象。

#### `get_proxy(protocol)`

`fetch_proxy_list` + `pick_proxy` 的快捷封装，一步拿到可用代理。

#### `verify_with_requests(proxy, timeout)`

用 requests 快速验证代理连通性，依次尝试以下地址，返回出口 IP 文本：

- `http://myip.ipip.net`（神龙官方 demo 同款）
- `https://api.ipify.org?format=json`
- `https://httpbin.org/ip`

---

### `verify_proxy.py`

Playwright 浏览器验证主脚本，执行流程：

1. 调用 `fetch_proxy_list()` 提取代理列表并打印前 5 个
2. 随机选取一个代理
3. 启动 Chromium（有头模式，窗口可见）
4. 依次访问 IP 查询页面：
   - `https://www.ip138.com/`
   - `https://whatismyipaddress.com/`
   - `https://browserleaks.com/ip`
5. 每个页面：自动提取 IP、截图保存至 `/tmp/`、等待用户按 Enter 继续
6. 代理连接失败时自动从列表换一个重试

浏览器启动参数：
- User-Agent：Windows Chrome 125（降低机器人识别概率）
- 语言：`en-US`，时区：`America/New_York`（与美国 IP 匹配）
- 视口：1280×800

---

### `run.sh`

一键启动脚本，执行 4 个步骤：

```
[1/4] 创建虚拟环境（--system-site-packages 继承全局依赖）
[2/4] 检查 requests / playwright 是否已安装，缺则补装
[3/4] 检查 Playwright Chromium 是否已安装，未装则自动安装
[4/4] 运行 verify_proxy.py
```

虚拟环境创建后复用，第二次运行直接跳过创建步骤。

---

## 在自己的代码中集成

```python
from shenlong_proxy import get_proxy, verify_with_requests

# 获取一个代理
proxy = get_proxy()

# 用于 requests
import requests
resp = requests.get("https://example.com", proxies=proxy.to_requests_proxies(), timeout=15)

# 用于 Playwright
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(proxy=proxy.to_playwright_proxy())
    page = context.new_page()
    page.goto("https://example.com")
```

---

## 常见问题

**Q: 运行后提示连接超时或代理不可用？**

检查 Clash Verge 是否已开启全局代理，端口是否为 `7897`。神龙代理必须在境外网络环境下才能使用。

**Q: 如何确认 Clash Verge 端口？**

打开 Clash Verge → 设置 → 混合端口，默认为 `7897`。若不同，修改 `shenlong_proxy.py` 中的 `CLASH_PROXY`。

**Q: IP 被识别为数据中心 IP 而非住宅 IP？**

正常现象，偶发。神龙 API 每次随机从资源池选取，可多运行几次换一个 IP，或减小 `ts`（有效期）参数让 IP 更新更快。

**Q: 如何批量验证多个 IP 的可用性？**

```python
from shenlong_proxy import fetch_proxy_list, pick_proxy, verify_with_requests

lines = fetch_proxy_list()
for _ in range(5):
    proxy = pick_proxy(lines)
    try:
        result = verify_with_requests(proxy, timeout=10)
        print(f"可用：{proxy.to_url()} -> {result}")
    except ConnectionError:
        print(f"不可用：{proxy.to_url()}")
```
