#!/usr/bin/env python3
"""
PC装机助手 — 多平台价格爬虫 (aiohttp 轻量版)

- 京东: so.m.jd.com 移动端搜索 API
- 淘宝: s.m.taobao.com 移动端
- 拼多多: mobile.yangkeduo.com 移动端
- 闲鱼: goofish.com / s.2.taobao.com
- 什么值得买: search.smzdm.com

输出 prices.json 到同目录，格式与 pc-builder.html 兼容。
GitHub Actions 环境下各大平台大概率触发验证码/反爬，脚本会尽可能抓取，
抓不到的品类保留现有数据不变。
"""

import asyncio
import aiohttp
import json
import os
import re
import sys
import time
import random
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ─── 配置 ───────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
PRICES_FILE = SCRIPT_DIR / "prices.json"
CST = timezone(timedelta(hours=8))

# 请求超时 & 并发
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
MAX_CONCURRENT = 4          # 同一平台并发数
PLATFORM_DELAY = (1.5, 3.0) # 平台间随机延迟(秒)
RETRY_COUNT = 2             # 单请求重试次数

# ─── 产品搜索词典 ───────────────────────────────────────

PRODUCTS: dict[str, list[str]] = {
    "cpu": [
        "R5 7500F 盒装", "R5 7500F 散片", "i5-14600KF 盒装",
        "Ryzen 7 7800X3D 散片", "i5-12600KF 盒装", "R5 9600X 散片",
        "R5 9600X 盒装", "i5-13600KF 盒装", "Ryzen 7 9800X3D 散片",
        "Ryzen 7 9800X3D 盒装", "i7-14700KF 盒装", "i9-14900K 盒装",
    ],
    "gpu": [
        "RTX 4060 8G", "RTX 4060 Ti 8G", "RX 7800 XT 16G",
        "RTX 4070 Super 12G", "RTX 5070 12G", "Arc B580 12G",
        "RX 9070 GRE 12G", "RX 9070 XT 16G", "RTX 5080 16G",
    ],
    "ram": [
        "金百达 DDR5 6000 16GB", "金百达银爵 DDR5 6000 32GB C30",
        "DDR4 32GB 3200MHz 套条", "金士顿FURY DDR5 6000 32GB",
        "芝奇幻锋戟 DDR5 6400 32GB CL32",
    ],
    "storage": [
        "1TB NVMe PCIe4.0", "致态 TiPlus7100 1TB",
        "梵想 S790C 2TB PCIe4.0", "三星 990 PRO 2TB",
        "致态 TiPlus7100s 2TB NVMe", "致态 TiPro9000 2TB PCIe5.0",
    ],
    "mobo": [
        "七彩虹 B760M DDR5 WIFI", "微星 B760M 爆破弹 WIFI D4",
        "华硕 B650M-K", "华硕 B760M-AYW WIFI D5",
        "微星 MAG B760M MORTAR WIFI II DDR5",
        "华硕 TUF GAMING B760M-PLUS WIFI D5", "技嘉 B850M 主板",
        "微星 Z790-A MAX WIFI", "技嘉 B650M 电竞雕", "华硕 X870E 主板",
    ],
    "psu": [
        "利民 TR-TG850-W 金牌全模组 850W", "鑫谷 GD850W 金牌 ATX3.1",
        "航嘉 WD850K 金牌全模组 850W", "艾湃电竞 DPS 850W 金牌全模组",
        "酷冷至尊雷霆金 850W 金牌", "艾湃电竞 DPS 1000W 金牌全模组",
        "SFX 750W 金牌电源", "航嘉 MVP K850 金牌 850W",
    ],
}

# ─── 移动端 User-Agent 池 ───────────────────────────────

UA_POOL = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.119 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.119 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/122.0.6261.89 Mobile/15E148 Safari/604.1",
]


def random_ua() -> str:
    return random.choice(UA_POOL)


# ─── 每个平台的搜索端点与解析器 ──────────────────────────

PLATFORM_CONFIG = {
    "京东": {
        "search_url": "https://so.m.jd.com/ware/search.action",
        "params": lambda kw: {"keyword": kw, "searchFrom": "home"},
        "headers": lambda: {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://so.m.jd.com/",
        },
        # 从 HTML 里用正则抠价格
        "parse": "jd_mobile_html",
    },
    "淘宝": {
        "search_url": "https://s.m.taobao.com/search",
        "params": lambda kw: {"q": kw, "search_type": "item"},
        "headers": lambda: {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://s.m.taobao.com/",
        },
        "parse": "taobao_mobile_html",
    },
    "拼多多": {
        "search_url": "https://mobile.yangkeduo.com/search_result.html",
        "params": lambda kw: {"search_key": kw},
        "headers": lambda: {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://mobile.yangkeduo.com/",
        },
        "parse": "pdd_mobile_html",
    },
    "闲鱼": {
        # goofish.com 是闲鱼新域名
        "search_url": "https://s.2.taobao.com/list/list.htm",
        "params": lambda kw: {"q": kw, "search_type": "item"},
        "headers": lambda: {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        "parse": "xianyu_mobile_html",
    },
    "什么值得买": {
        "search_url": "https://search.smzdm.com/",
        "params": lambda kw: {"c": "home", "s": kw, "order": "score", "v": "b"},
        "headers": lambda: {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.smzdm.com/",
        },
        "parse": "smzdm_html",
    },
}

# ─── 价格提取正则 ───────────────────────────────────────

# 京东移动端：页面中常见 ¥xxx.xx 或 ￥xxx.xx
RE_JD_PRICE = re.compile(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)")
# 淘宝/拼多多/闲鱼：类似
RE_TB_PRICE = re.compile(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)")
# 什么值得买：<span class="red">¥xxx</span> 或类似
RE_SMZDM_PRICE = re.compile(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)")

# 屏蔽价（异常值）
PRICE_MIN = 50        # 低于此视为无效
PRICE_MAX = 99999     # 高于此视为无效


def parse_price_from_html(html: str, platform: str) -> Optional[float]:
    """从 HTML 文本中提取第一个有效价格。"""
    if platform == "什么值得买":
        prices = RE_SMZDM_PRICE.findall(html)
    elif platform == "京东":
        prices = RE_JD_PRICE.findall(html)
    else:
        prices = RE_TB_PRICE.findall(html)

    for p in prices:
        try:
            val = float(p)
            if PRICE_MIN <= val <= PRICE_MAX:
                return val
        except ValueError:
            continue
    return None


# ─── HTTP 会话与请求 ────────────────────────────────────

async def fetch(
    session: aiohttp.ClientSession,
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> Optional[str]:
    """带重试的 GET 请求，返回文本或 None。"""
    for attempt in range(RETRY_COUNT + 1):
        try:
            async with session.get(
                url, params=params, headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    return await resp.text(encoding="utf-8", errors="replace")
                elif resp.status in (302, 301):
                    # 重定向到登录/验证页 → 放弃
                    return None
                else:
                    if attempt < RETRY_COUNT:
                        await asyncio.sleep(2 ** attempt)
        except (asyncio.TimeoutError, aiohttp.ClientError):
            if attempt < RETRY_COUNT:
                await asyncio.sleep(2 ** attempt)
    return None


# ─── 单品搜索 ───────────────────────────────────────────

async def search_product(
    session: aiohttp.ClientSession,
    platform: str,
    keyword: str,
    part_type: str,
    sem: asyncio.Semaphore,
) -> dict | None:
    """搜索单个产品并返回 raw_prices 条目，失败返回 None。"""
    cfg = PLATFORM_CONFIG[platform]
    url = cfg["search_url"]
    params = cfg["params"](keyword)
    headers = cfg["headers"]()

    async with sem:
        # 平台内友好延迟
        await asyncio.sleep(random.uniform(0.3, 0.8))
        html = await fetch(session, url, params=params, headers=headers)

    if not html:
        return None

    price = parse_price_from_html(html, platform)
    if price is None:
        return None

    return {
        "platform": platform,
        "part_type": part_type,
        "part_name": keyword,
        "price": price,
        "url": f"{url}?{ '&'.join(f'{k}={v}' for k,v in params.items()) }",
        "shop": f"{platform}",
    }


# ─── 主爬虫逻辑 ─────────────────────────────────────────

async def scrape_all() -> dict:
    """并发抓取全部平台 × 全部品类 × 全部产品，返回 raw_prices 列表。"""
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = []

    connector = aiohttp.TCPConnector(
        limit=20,
        limit_per_host=6,
        ttl_dns_cache=300,
        ssl=False,
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        for platform in PLATFORM_CONFIG:
            # 平台间随机延迟
            await asyncio.sleep(random.uniform(*PLATFORM_DELAY))
            for part_type, products in PRODUCTS.items():
                for product_name in products:
                    tasks.append(
                        search_product(session, platform, product_name, part_type, sem)
                    )
        results = await asyncio.gather(*tasks, return_exceptions=True)

    raw_prices = []
    success_count = 0
    fail_count = 0

    for r in results:
        if isinstance(r, Exception):
            fail_count += 1
        elif r is not None:
            raw_prices.append(r)
            success_count += 1
        else:
            fail_count += 1

    total = len(results)
    print(f"抓取完成: {total} 个目标, 成功 {success_count}, 失败 {fail_count}")
    return raw_prices


# ─── 数据合并与格式化 ────────────────────────────────────

def load_existing() -> dict | None:
    """加载已有 prices.json，不存在则返回 None。"""
    if not PRICES_FILE.exists():
        return None
    try:
        with open(PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def build_prices_json(raw_prices: list[dict]) -> dict:
    """
    将 raw_prices 列表组装为完整 prices.json 结构：
    { updated, sources, parts: {cpu/gpu/ram/storage/mobo/psu}, raw_prices }
    """
    # 合并新旧 raw_prices：新数据覆盖同名同平台条目
    existing = load_existing()
    old_raw = existing.get("raw_prices", []) if existing else []

    # 以 (platform, part_type, part_name) 为去重键，新数据优先
    seen = set()
    merged_raw = []

    for item in raw_prices:
        key = (item["platform"], item["part_type"], item["part_name"])
        if key not in seen:
            seen.add(key)
            merged_raw.append(item)

    for item in old_raw:
        key = (item["platform"], item["part_type"], item["part_name"])
        if key not in seen:
            seen.add(key)
            merged_raw.append(item)

    # 按品类 + 价格排序
    merged_raw.sort(key=lambda x: (x["part_type"], x["price"]))

    # 构建 parts 结构
    parts: dict[str, list[dict]] = {
        part_type: [] for part_type in PRODUCTS
    }

    # 按产品名分组
    name_index: dict[tuple[str, str], list[dict]] = {}
    for item in merged_raw:
        key = (item["part_type"], item["part_name"])
        name_index.setdefault(key, []).append(item)

    for (ptype, pname), entries in name_index.items():
        platforms_list = []
        for e in entries:
            platforms_list.append({
                "platform": e["platform"],
                "price": e["price"],
                "shop": e.get("shop", e["platform"]),
                "url": e.get("url", ""),
            })
        # 找最低价
        best = min(platforms_list, key=lambda x: x["price"])
        parts[ptype].append({
            "name": pname,
            "platforms": platforms_list,
            "best_price": best["price"],
            "best_platform": best["platform"],
        })

    # 收集所有出现的平台作为 sources
    sources_set: set[str] = set()
    for item in merged_raw:
        sources_set.add(item["platform"])
    sources = sorted(sources_set, key=lambda s: ["京东","淘宝","拼多多","闲鱼","什么值得买"].index(s) if s in ["京东","淘宝","拼多多","闲鱼","什么值得买"] else 99)

    now_str = datetime.now(CST).isoformat()

    return {
        "updated": now_str,
        "sources": sources,
        "parts": parts,
        "raw_prices": merged_raw,
    }


# ─── 入口 ───────────────────────────────────────────────

async def main():
    print(f"[{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}] 价格爬虫启动")
    print(f"目标: {sum(len(v) for v in PRODUCTS.values())} 个产品 × {len(PLATFORM_CONFIG)} 个平台")

    raw_prices = await scrape_all()
    new_count = len(raw_prices)

    existing = load_existing()
    old_count = len(existing.get("raw_prices", [])) if existing else 0
    print(f"新抓取: {new_count} 条, 现有: {old_count} 条")

    data = build_prices_json(raw_prices)
    total = len(data["raw_prices"])
    print(f"合并后总计: {total} 条")

    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f"已写入 {PRICES_FILE}")

    # 简要统计
    for pt in ["cpu", "gpu", "ram", "storage", "mobo", "psu"]:
        count = len(data["parts"].get(pt, []))
        print(f"  {pt}: {count} 个产品")


if __name__ == "__main__":
    asyncio.run(main())
