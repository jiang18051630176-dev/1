"""
PC装机助手 — 多平台实时价格服务
数据源: 京东 / 淘宝 / 拼多多 / 闲鱼 / 什么值得买
用法: python price_server.py → http://localhost:3000
"""
import asyncio
import json
import os
import sys
import time
import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).parent
DB_PATH = ROOT / "price_cache.db"

# 爬取状态
scrape_status = {
    'running': False,
    'platform': '',
    'started_at': None,
    'finished_at': None,
    'results': {},
    'errors': [],
}
scrape_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=2)

# ============================================================
# 搜索关键词库 - 覆盖主流热门硬件
# ============================================================
SEARCH_KEYWORDS = {
    'cpu': [
        'i5-13600KF 盒装', 'i5-14600KF', 'i7-14700KF', 'i9-14900K',
        '7500F 盒装', '7800X3D', '9800X3D', '9950X3D',
        'Ryzen 5 9600X', 'Ryzen 7 9700X',
    ],
    'gpu': [
        'RTX 4060', 'RTX 4060 Ti', 'RTX 4070 Super',
        'RTX 5070', 'RTX 5080', 'RTX 5090',
        'RX 9070 XT', 'RX 7900 XT', 'RX 7800 XT',
        'Arc B580',
    ],
    'ram': [
        'DDR5 32GB 6000MHz 套条', 'DDR5 16GB 6000MHz',
        'DDR5 32GB 6400MHz', 'DDR4 32GB 3200MHz',
    ],
    'storage': [
        '2TB NVMe SSD PCIe 4.0', '1TB NVMe SSD',
        '三星 990 PRO 2TB', '致态 TiPlus7100 1TB',
    ],
    'mobo': [
        'B760M 主板 DDR5', 'Z790 主板', 'B650M 主板',
        'X870E 主板', 'B850 主板',
    ],
    'psu': [
        '金牌电源 850W', '白金电源 1000W',
        'SFX电源 750W', 'ATX3.1电源',
    ],
}

# ============================================================
# 缓存数据库
# ============================================================
class PriceCache:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    part_type TEXT NOT NULL,
                    part_name TEXT NOT NULL,
                    price REAL NOT NULL,
                    url TEXT,
                    shop_name TEXT,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(platform, part_type, part_name)
                );
                CREATE INDEX IF NOT EXISTS idx_prices_lookup 
                    ON prices(platform, part_type, part_name);
                CREATE INDEX IF NOT EXISTS idx_prices_time 
                    ON prices(scraped_at);
            """)
            self.conn.commit()

    def put_price(self, platform, part_type, part_name, price, url='', shop=''):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO prices (platform, part_type, part_name, price, url, shop_name, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (platform, part_type, part_name[:200], price, url or '', shop or ''))
            self.conn.commit()

    def get_prices(self, platform=None, part_type=None, max_age_hours=24):
        with self.lock:
            conditions = ["scraped_at > datetime('now', '-' || ? || ' hours')"]
            params = [str(max_age_hours)]
            if platform:
                conditions.append("platform = ?")
                params.append(platform)
            if part_type:
                conditions.append("part_type = ?")
                params.append(part_type)
            where = " AND ".join(conditions)
            rows = self.conn.execute(
                f"SELECT platform, part_type, part_name, price, url, shop_name FROM prices WHERE {where} ORDER BY price ASC",
                params
            ).fetchall()
        return [{
            'platform': r[0], 'part_type': r[1], 'part_name': r[2],
            'price': r[3], 'url': r[4], 'shop': r[5],
        } for r in rows]

    def get_stats(self):
        with self.lock:
            rows = self.conn.execute("""
                SELECT platform, COUNT(*) as cnt, MIN(scraped_at), MAX(scraped_at) 
                FROM prices GROUP BY platform
            """).fetchall()
        return [{'platform': r[0], 'count': r[1], 'oldest': r[2], 'newest': r[3]} for r in rows]


cache = PriceCache()

# ============================================================
# 京东爬虫 - 使用 aiohttp 直连移动端 API
# ============================================================
try:
    import aiohttp
    from urllib.parse import quote as urlquote
except ImportError:
    aiohttp = None
    urlquote = None

async def jd_search_mobile(keyword):
    """京东移动端搜索 API - 无需浏览器"""
    if aiohttp is None:
        return []
    items = []
    try:
        encoded = urlquote(keyword)
        url = f'https://so.m.jd.com/ware/search.action?keyword={encoded}&page=1&pageSize=10&sf=11&as=1'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://so.m.jd.com/',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), 
                                    ssl=False, allow_redirects=True) as resp:
                text = await resp.text()
                
                # 尝试从 JS 变量中提取商品列表
                import re
                # 提取 wareList
                m = re.search(r'wareList\s*[:=]\s*(\[[^\]]+\])', text)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        for w in data:
                            name = w.get('warename', w.get('wname', ''))
                            price = float(w.get('jdPrice', w.get('jdprice', 0)))
                            if name and price > 0:
                                items.append({
                                    'name': name, 'price': price,
                                    'url': f"https://item.m.jd.com/product/{w.get('wareId','')}.html",
                                    'shop': w.get('shopname', '京东')
                                })
                    except: pass
                
                # 尝试提取 price 相关 JSON
                if not items:
                    prices = re.findall(r'"jdPrice"\s*:\s*"?(\d+\.?\d*)"?', text)
                    names = re.findall(r'"warename"\s*:\s*"([^"]+)"', text)
                    for i in range(min(len(names), len(prices))):
                        p = float(prices[i])
                        if p > 0 and p < 50000:
                            items.append({'name': names[i], 'price': p, 'url': '', 'shop': '京东'})
    except Exception as e:
        print(f"  [JD API] {keyword[:20]}... {e}")
    
    return items[:8]


async def jd_scrape_all():
    print("\n🛒 [京东] 开始爬取...")
    all_items = []
    for part_type, keywords in SEARCH_KEYWORDS.items():
        for kw in keywords:
            items = await jd_search_mobile(kw)
            for item in items:
                item['part_type'] = part_type
                item['platform'] = '京东'
            all_items.extend(items)
            if items:
                print(f"  [JD] {kw[:25]:25s} -> {len(items)}条")
            await asyncio.sleep(1.5)
    
    for item in all_items:
        cache.put_price('京东', item['part_type'], item['name'], 
                       item['price'], item.get('url', ''), item.get('shop', ''))
    
    print(f"  [京东] 完成，共 {len(all_items)} 条\n")
    return all_items


# ============================================================
# 什么值得买聚合爬虫 (Playwright + Stealth)
# ============================================================
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None
try:
    from playwright_stealth import Stealth
except ImportError:
    Stealth = None

SMZDM_SEARCH_KEYWORDS = [
    # CPU
    'i5-13600KF', 'i5-14600KF', 'i7-14700KF', '7500F', '7800X3D', '9800X3D',
    # GPU  
    'RTX 4060', 'RTX 4060 Ti', 'RTX 4070 Super', 'RTX 5070', 'RX 9070 XT',
    # RAM
    'DDR5 32G 6000', 'DDR5 16G 6000',
    # Storage
    '2T NVMe', '1T NVMe', '990 PRO',
    # Mobo
    'B760M DDR5', 'B650M',
    # PSU
    '金牌电源 850W', '白金电源 1000W',
]

async def smzdm_scrape_all():
    if async_playwright is None:
        return []
    print("\n📦 [什么值得买] 开始爬取 (Playwright)...")
    all_items = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
            ]
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        
        for kw in SMZDM_SEARCH_KEYWORDS:
            try:
                url = f'https://search.smzdm.com/?c=home&s={kw}&order=score&v=b'
                await page.goto(url, timeout=20000, wait_until='domcontentloaded')
                await asyncio.sleep(2)
                
                # 提取商品
                items = await page.evaluate("""
                    () => {
                        const results = [];
                        const feeds = document.querySelectorAll('.feed-row-wide, .feed-row, .feed-block-ver');
                        feeds.forEach(feed => {
                            const titleEl = feed.querySelector('.feed-block-title a, .feed-ver-title a, h5 a');
                            const priceEl = feed.querySelector('.feed-block-price .z-highlight, .z-highlight, .feed-block-extras');
                            const mallEl = feed.querySelector('.feed-block-mall span, .feed-block-title span:last-child, .z-mall');
                            
                            const name = titleEl ? titleEl.textContent.trim().substring(0, 80) : '';
                            const priceText = priceEl ? priceEl.textContent.trim() : '';
                            const price = parseFloat(priceText.replace(/[^\\d.]/g, '')) || 0;
                            const mall = mallEl ? mallEl.textContent.trim() : '什么值得买';
                            
                            if (name && price > 10 && price < 50000) {
                                results.push({name, price, mall});
                            }
                        });
                        if (results.length === 0) {
                            // 备选: 全文提取
                            const text = document.body.innerText;
                            const lines = text.split('\\n');
                            for (let i = 0; i < lines.length - 1; i++) {
                                const pm = lines[i].match(/[¥￥]\\s*([\\d,]+(\\.\\d{1,2})?)/);
                                if (pm && lines[i-1] && lines[i-1].length > 6) {
                                    const p = parseFloat(pm[1].replace(/,/g, ''));
                                    if (p > 10 && p < 50000) {
                                        results.push({name: lines[i-1].substring(0, 80), price: p, mall: '什么值得买'});
                                    }
                                }
                            }
                        }
                        return results.slice(0, 5);
                    }
                """)
                
                for item in items:
                    all_items.append({
                        'name': item['name'], 'price': item['price'],
                        'platform': '什么值得买', 'shop': item.get('mall', '什么值得买'),
                        'part_type': '',
                    })
                
                if items:
                    print(f"  [SMZDM] {kw[:25]:25s} -> {len(items)}条")
            except Exception as e:
                print(f"  [SMZDM] {kw[:20]}... {e}")
            
            await asyncio.sleep(2)
        
        await browser.close()
    
    for item in all_items:
        cache.put_price('什么值得买', item['part_type'], item['name'],
                       item['price'], '', item.get('shop', ''))
    
    print(f"  [什么值得买] 完成，共 {len(all_items)} 条\n")
    return all_items


# ============================================================
# 闲鱼爬虫
# ============================================================
async def xianyu_search(keyword):
    """闲鱼搜索 - 使用 goofish 域名"""
    if aiohttp is None:
        return []
    items = []
    try:
        encoded = urlquote(keyword)
        url = f'https://www.goofish.com/search?q={encoded}'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
            'Accept': 'text/html,application/xhtml+xml',
        }
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                    ssl=False) as resp:
                text = await resp.text()
                
                import re
                # 提取价格
                prices = re.findall(r'[¥￥]\s*([\d,]+(?:\.\d{1,2})?)', text)
                # 尝试匹配标题
                titles = re.findall(r'"title"\s*:\s*"([^"]+)"', text)
                
                for i in range(min(len(titles), len(prices))):
                    p = float(prices[i].replace(',', ''))
                    if 50 < p < 30000 and len(titles[i]) > 4:
                        items.append({
                            'name': titles[i][:80], 'price': p,
                            'url': '', 'shop': '闲鱼二手'
                        })
    except Exception as e:
        print(f"  [闲鱼] {keyword[:20]}... {e}")
    
    return items[:5]


async def xianyu_scrape_all():
    print("\n🐟 [闲鱼] 开始爬取...")
    all_items = []
    for part_type, keywords in SEARCH_KEYWORDS.items():
        for kw in keywords:
            items = await xianyu_search(kw)
            for item in items:
                item['part_type'] = part_type
                item['platform'] = '闲鱼'
            all_items.extend(items)
            if items:
                print(f"  [闲鱼] {kw[:25]:25s} -> {len(items)}条")
            await asyncio.sleep(2)
    
    for item in all_items:
        cache.put_price('闲鱼', item['part_type'], item['name'],
                       item['price'], item.get('url', ''), item.get('shop', ''))
    
    print(f"  [闲鱼] 完成，共 {len(all_items)} 条\n")
    return all_items


# ============================================================
# 淘宝爬虫
# ============================================================
async def taobao_search(keyword):
    """淘宝移动端搜索"""
    if aiohttp is None:
        return []
    items = []
    try:
        encoded = urlquote(keyword)
        url = f'https://s.m.taobao.com/h5?q={encoded}&search_type=item'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
        }
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                    ssl=False) as resp:
                text = await resp.text()
                
                import re
                # 提取价格
                prices = re.findall(r'"price"\s*:\s*"?([\d.]+)"?', text)
                titles = re.findall(r'"title"\s*:\s*"([^"]+)"', text)
                
                for i in range(min(len(titles), len(prices))):
                    p = float(prices[i])
                    if 10 < p < 50000 and len(titles[i]) > 4:
                        items.append({
                            'name': titles[i][:80], 'price': p,
                            'url': '', 'shop': '淘宝'
                        })
    except Exception as e:
        print(f"  [淘宝] {keyword[:20]}... {e}")
    
    return items[:5]


async def taobao_scrape_all():
    print("\n🔵 [淘宝] 开始爬取...")
    all_items = []
    for part_type, keywords in SEARCH_KEYWORDS.items():
        for kw in keywords:
            items = await taobao_search(kw)
            for item in items:
                item['part_type'] = part_type
                item['platform'] = '淘宝'
            all_items.extend(items)
            if items:
                print(f"  [淘宝] {kw[:25]:25s} -> {len(items)}条")
            await asyncio.sleep(2)
    
    for item in all_items:
        cache.put_price('淘宝', item['part_type'], item['name'],
                       item['price'], item.get('url', ''), item.get('shop', ''))
    
    print(f"  [淘宝] 完成，共 {len(all_items)} 条\n")
    return all_items


# ============================================================
# 拼多多爬虫
# ============================================================
async def pdd_search(keyword):
    """拼多多移动端搜索"""
    if aiohttp is None:
        return []
    items = []
    try:
        encoded = urlquote(keyword)
        url = f'https://mobile.yangkeduo.com/search_result.html?search_key={encoded}'
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
        }
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                    ssl=False) as resp:
                text = await resp.text()
                
                import re
                prices = re.findall(r'"price"\s*:\s*(\d+)', text)
                titles = re.findall(r'"goods_name"\s*:\s*"([^"]+)"', text)
                
                for i in range(min(len(titles), len(prices))):
                    p = float(prices[i]) / 100  # PDD price is in cents
                    if 10 < p < 50000 and len(titles[i]) > 4:
                        items.append({
                            'name': titles[i][:80], 'price': p,
                            'url': '', 'shop': '拼多多'
                        })
    except Exception as e:
        print(f"  [拼多多] {keyword[:20]}... {e}")
    
    return items[:5]


async def pdd_scrape_all():
    print("\n🔴 [拼多多] 开始爬取...")
    all_items = []
    for part_type, keywords in SEARCH_KEYWORDS.items():
        for kw in keywords:
            items = await pdd_search(kw)
            for item in items:
                item['part_type'] = part_type
                item['platform'] = '拼多多'
            all_items.extend(items)
            if items:
                print(f"  [拼多多] {kw[:25]:25s} -> {len(items)}条")
            await asyncio.sleep(2)
    
    for item in all_items:
        cache.put_price('拼多多', item['part_type'], item['name'],
                       item['price'], item.get('url', ''), item.get('shop', ''))
    
    print(f"  [拼多多] 完成，共 {len(all_items)} 条\n")
    return all_items


# ============================================================
# 价格聚合引擎
# ============================================================
def aggregate_prices():
    """聚合多平台价格，按配件归类"""
    raw = cache.get_prices(max_age_hours=168)
    
    # 按 part_type -> part_name -> [platforms] 分组
    grouped = {}
    for item in raw:
        pt = item['part_type']
        pn = item['part_name']
        if pt not in grouped:
            grouped[pt] = {}
        if pn not in grouped[pt]:
            grouped[pt][pn] = []
        grouped[pt][pn].append({
            'platform': item['platform'],
            'price': item['price'],
            'shop': item.get('shop', ''),
            'url': item.get('url', ''),
        })
    
    # 计算每个配件的最低价格
    result = {}
    for pt, names in grouped.items():
        result[pt] = []
        for pn, platforms in names.items():
            if len(platforms) < 2:
                continue  # 跳过只有单一平台的
            prices = sorted(platforms, key=lambda x: x['price'])
            result[pt].append({
                'name': pn,
                'platforms': prices,
                'lowest': prices[0] if prices else None,
                'highest': prices[-1] if len(prices) > 1 else None,
                'spread': round(prices[-1]['price'] - prices[0]['price'], 2) if len(prices) > 1 else 0,
            })
    
    return result


# ============================================================
# 后台爬取执行器
# ============================================================
def run_scrape_in_background(platform):
    global scrape_status
    with scrape_lock:
        if scrape_status['running']:
            return False
        scrape_status['running'] = True
        scrape_status['platform'] = platform
        scrape_status['started_at'] = datetime.now().isoformat()
        scrape_status['finished_at'] = None
        scrape_status['results'] = {}
        scrape_status['errors'] = []
    
    async def do_scrape():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = {}
            
            if platform in ('jd', 'all'):
                try:
                    r = await jd_scrape_all()
                    results['jd'] = len(r)
                except Exception as e:
                    results['jd'] = f'ERROR: {e}'
                    scrape_status['errors'].append(f'JD: {e}')
            
            if platform in ('smzdm', 'all'):
                try:
                    r = await smzdm_scrape_all()
                    results['smzdm'] = len(r)
                except Exception as e:
                    results['smzdm'] = f'ERROR: {e}'
                    scrape_status['errors'].append(f'SMZDM: {e}')
            
            if platform in ('xianyu', 'all'):
                try:
                    r = await xianyu_scrape_all()
                    results['xianyu'] = len(r)
                except Exception as e:
                    results['xianyu'] = f'ERROR: {e}'
                    scrape_status['errors'].append(f'闲鱼: {e}')
            
            if platform in ('taobao', 'all'):
                try:
                    r = await taobao_scrape_all()
                    results['taobao'] = len(r)
                except Exception as e:
                    results['taobao'] = f'ERROR: {e}'
                    scrape_status['errors'].append(f'淘宝: {e}')
            
            if platform in ('pdd', 'all'):
                try:
                    r = await pdd_scrape_all()
                    results['pdd'] = len(r)
                except Exception as e:
                    results['pdd'] = f'ERROR: {e}'
                    scrape_status['errors'].append(f'拼多多: {e}')
            
            with scrape_lock:
                scrape_status['results'] = results
                scrape_status['finished_at'] = datetime.now().isoformat()
                scrape_status['running'] = False
            print(f"\n✅ 后台爬取完成: {results}")
        except Exception as e:
            with scrape_lock:
                scrape_status['errors'].append(f'FATAL: {e}')
                scrape_status['running'] = False
                scrape_status['finished_at'] = datetime.now().isoformat()
        finally:
            loop.close()
    
    executor.submit(lambda: asyncio.run(do_scrape()))
    return True


# ============================================================
# HTTP 服务器
# ============================================================
class PriceServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def _send_file(self, path, content_type):
        try:
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._send_json({'error': 'File not found'}, 404)
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        
        # 聚合价格
        if path == '/api/prices':
            platform = params.get('platform', [None])[0]
            part_type = params.get('type', [None])[0]
            prices = cache.get_prices(platform=platform, part_type=part_type, max_age_hours=168)
            self._send_json({'updated': datetime.now().isoformat(), 'total': len(prices), 'prices': prices})
            return
        
        # 聚合比价
        if path == '/api/compare':
            result = aggregate_prices()
            self._send_json({'updated': datetime.now().isoformat(), 'compare': result})
            return
        
        # 触发爬取（后台异步）
        if path == '/api/scrape':
            platform = params.get('platform', ['all'])[0]
            with scrape_lock:
                if scrape_status['running']:
                    self._send_json({
                        'status': 'busy', 'message': f'正在爬取 {scrape_status["platform"]}',
                        'started_at': scrape_status['started_at'],
                    })
                    return
            ok = run_scrape_in_background(platform)
            self._send_json({
                'status': 'started' if ok else 'error',
                'platform': platform,
                'message': f'已启动 {platform} 爬取' if ok else '启动失败',
                'started_at': scrape_status.get('started_at'),
            })
            return
        
        # 爬取状态
        if path == '/api/scrape/status':
            with scrape_lock:
                self._send_json(dict(scrape_status))
            return
        
        # 缓存统计
        if path == '/api/stats':
            stats = cache.get_stats()
            self._send_json({
                'stats': stats,
                'db_size': os.path.getsize(DB_PATH) if DB_PATH.exists() else 0,
                'scrape': dict(scrape_status),
            })
            return
        
        # 价格导出（前端 API）
        if path == '/api/prices/all':
            result = aggregate_prices()
            output = {
                'updated': datetime.now().isoformat(),
                'sources': ['京东', '淘宝', '拼多多', '闲鱼', '什么值得买'],
                'parts': {},
                'raw_prices': [],
            }
            for pt, items in result.items():
                output['parts'][pt] = []
                for item in items[:30]:
                    entry = {'name': item['name'], 'platforms': item['platforms']}
                    if item['lowest']:
                        entry['best_price'] = item['lowest']['price']
                        entry['best_platform'] = item['lowest']['platform']
                    output['parts'][pt].append(entry)
            output['raw_prices'] = cache.get_prices(max_age_hours=168)
            self._send_json(output)
            return
        
        # 静态文件
        if path == '/' or path == '/pc-builder.html':
            self._send_file(ROOT / 'pc-builder.html', 'text/html; charset=utf-8')
            return
        if path == '/prices.json':
            self._send_file(ROOT / 'prices.json', 'application/json')
            return
        
        self._send_json({'error': 'Not found'}, 404)
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()


if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 3000))
    server = HTTPServer(('0.0.0.0', PORT), PriceServer)
    print(f"""
{'='*60}
  PC装机助手 - 多平台实时价格服务
  已启动: http://localhost:{PORT}
  
  API 端点:
    GET /api/prices        - 聚合价格查询
    GET /api/prices/all    - 前端价格数据
    GET /api/compare       - 多平台比价
    GET /api/scrape?platform=all|jd|tb|pdd|xy|smzdm - 触发爬取
    GET /api/scrape/status - 爬取状态
    GET /api/stats         - 缓存统计
{'='*60}
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        print("\n已停止")
