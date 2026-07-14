// refresh_prices.js — 定时硬件价格采集脚本
// 用法: node refresh_prices.js [--schedule weekly]
// 覆盖: CPU/GPU/RAM/Storage/Mobo/PSU × 京东/淘宝/拼多多/闲鱼

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUTPUT = path.join(__dirname, 'prices.json');
const DB_PATH = path.join(__dirname, 'price_cache.db');

// 热门型号搜索关键词
const PRODUCTS = {
  cpu: [
    { name: 'R5 7500F 盒装', kw_jd: 'AMD 锐龙 R5 7500F 盒装', kw_tb: 'R5 7500F 盒装' },
    { name: 'R5 7500F 散片', kw_jd: 'R5 7500F 散片', kw_tb: 'R5 7500F 散片 CPU' },
    { name: 'R7 7800X3D 散片', kw_jd: 'R7 7800X3D 散片', kw_tb: '7800X3D 散片' },
    { name: 'i5-14600KF 盒装', kw_jd: 'i5 14600KF 盒装', kw_tb: '14600KF 盒装' },
    { name: 'i5-13600KF', kw_jd: 'i5 13600KF', kw_tb: '13600KF' },
    { name: 'Ultra 5 245KF', kw_jd: 'Ultra 5 245KF', kw_tb: '245KF' },
    { name: 'R5 9600X', kw_jd: 'R5 9600X 盒装', kw_tb: '9600X' },
  ],
  gpu: [
    { name: 'RTX 4060 8G', kw_jd: 'RTX 4060 8G 显卡', kw_tb: 'RTX4060' },
    { name: 'RTX 4060 Ti 8G', kw_jd: 'RTX 4060 Ti 8G', kw_tb: '4060Ti 8G' },
    { name: 'RTX 4070 Super 12G', kw_jd: 'RTX 4070 Super 12G', kw_tb: '4070Super' },
    { name: 'RTX 5060 8G', kw_jd: 'RTX 5060 8G', kw_tb: 'RTX5060' },
    { name: 'RX 7800 XT 16G', kw_jd: 'RX 7800 XT 16G', kw_tb: '7800XT' },
    { name: 'RX 9070 XT 16G', kw_jd: 'RX 9070 XT', kw_tb: '9070XT' },
    { name: 'Arc B580 12G', kw_jd: 'B580 显卡', kw_tb: 'B580' },
  ],
  ram: [
    { name: '金百达 DDR5 6000 16GB', kw_jd: '金百达 DDR5 6000 16G', kw_tb: '金百达 DDR5 6000' },
    { name: '金士顿 DDR5 6000 32GB', kw_jd: '金士顿 DDR5 6000 32G', kw_tb: '金士顿 DDR5 6000' },
    { name: '芝奇 DDR5 6000 32GB CL30', kw_jd: '芝奇 DDR5 6000 CL30 32G', kw_tb: '芝奇幻锋戟6000' },
    { name: '光威 DDR5 6000 32GB', kw_jd: '光威 DDR5 6000 32G', kw_tb: '光威DDR5' },
    { name: '海盗船 DDR5 6400 32GB', kw_jd: '海盗船 DDR5 6400 32G', kw_tb: '海盗船6400' },
  ],
  storage: [
    { name: '致态 TiPlus7100 1TB', kw_jd: '致态 TiPlus7100 1TB', kw_tb: 'TiPlus7100' },
    { name: '三星 990 PRO 1TB', kw_jd: '三星 990 PRO 1TB', kw_tb: '990PRO' },
    { name: '西数 SN7100 1TB', kw_jd: 'WD SN7100 1TB', kw_tb: 'SN7100' },
    { name: '金士顿 NV3 1TB', kw_jd: '金士顿 NV3 1TB', kw_tb: 'NV3' },
    { name: '三星 990 PRO 2TB', kw_jd: '三星 990 PRO 2TB', kw_tb: '990PRO 2TB' },
  ],
  mobo: [
    { name: 'MSI B650M-A WiFi', kw_jd: '微星 B650M-A WiFi', kw_tb: 'B650M' },
    { name: '华硕 B760M-PLUS D5', kw_jd: '华硕 B760M-PLUS WiFi D5', kw_tb: 'B760M' },
    { name: '华擎 B850M Pro-A', kw_jd: '华擎 B850M Pro-A', kw_tb: 'B850M' },
    { name: 'MSI Z790 Tomahawk D5', kw_jd: '微星 Z790 Tomahawk D5', kw_tb: 'Z790' },
    { name: 'MSI MAG Z890 Tomahawk', kw_jd: '微星 Z890 Tomahawk', kw_tb: 'Z890' },
  ],
  psu: [
    { name: '海韵 Focus GX-750', kw_jd: '海韵 Focus GX-750 金牌', kw_tb: '海韵750' },
    { name: '海韵 Focus GX-850', kw_jd: '海韵 Focus GX-850 金牌', kw_tb: '海韵850' },
    { name: '振华 Leadex III 650W', kw_jd: '振华 Leadex III 650W', kw_tb: '振华650' },
    { name: '长城 G7 750W', kw_jd: '长城 G7 750W 金牌', kw_tb: '长城G7' },
    { name: '酷冷至尊 MWE 550W', kw_jd: '酷冷至尊 MWE 550W', kw_tb: '酷冷550' },
  ],
};

// 平台搜索函数
async function searchJD(browser, keyword) {
  const page = await browser.newPage();
  try {
    await page.goto(`https://search.jd.com/Search?keyword=${encodeURIComponent(keyword)}&enc=utf-8`, { timeout: 20000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    const items = await page.evaluate(() =>
      [...document.querySelectorAll('.gl-item')].slice(0, 4).map(el => ({
        name: (el.querySelector('.p-name em') || el.querySelector('.p-name a'))?.textContent?.trim()?.slice(0, 60) || '',
        price: parseFloat((el.querySelector('.p-price i')?.textContent?.trim() || '').replace(/[^0-9.]/g, '')) || 0,
        shop: el.querySelector('.p-shop')?.textContent?.trim() || '京东'
      })).filter(x => x.price > 0)
    );
    return items.map(i => ({ ...i, platform: '京东' }));
  } catch(e) { return []; }
  finally { await page.close(); }
}

// 淘宝搜索 (用 maishou API)
async function searchTaobao(keyword) {
  try {
    const resp = await fetch('https://appapi.maishou88.com/api/v1/homepage/searchList', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'MaiShouApp/3.7.7', 'openid': '564bdce0fa408fc9e1d5d42fd022ef0b', 'version': '3.7.7.2' },
      body: new URLSearchParams({ isCoupon: '0', keyword, openid: '564bdce0fa408fc9e1d5d42fd022ef0b', order: 'desc', page: '1', sort: '', sourceType: '1', user_id: '' }),
      signal: AbortSignal.timeout(15000)
    });
    const data = await resp.json();
    return (data.data || []).slice(0, 4).map(v => ({
      name: (v.title || '').slice(0, 60),
      price: parseFloat(v.actualPrice) || 0,
      shop: v.shopName || '淘宝',
      platform: '淘宝'
    })).filter(x => x.price > 0);
  } catch(e) { return []; }
}

// 拼多多搜索 (用 maishou API)
async function searchPDD(keyword) {
  try {
    const resp = await fetch('https://appapi.maishou88.com/api/v1/homepage/searchList', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'MaiShouApp/3.7.7', 'openid': '564bdce0fa408fc9e1d5d42fd022ef0b', 'version': '3.7.7.2' },
      body: new URLSearchParams({ isCoupon: '0', keyword, openid: '564bdce0fa408fc9e1d5d42fd022ef0b', order: 'desc', page: '1', sort: '', sourceType: '3', user_id: '' }),
      signal: AbortSignal.timeout(15000)
    });
    const data = await resp.json();
    return (data.data || []).slice(0, 4).map(v => ({
      name: (v.title || '').slice(0, 60),
      price: parseFloat(v.actualPrice) || 0,
      shop: v.shopName || '拼多多',
      platform: '拼多多'
    })).filter(x => x.price > 0);
  } catch(e) { return []; }
}

// 闲鱼搜索 (用 goofish URL)
async function searchXianyu(browser, keyword) {
  const page = await browser.newPage();
  try {
    await page.goto(`https://www.goofish.com/search?q=${encodeURIComponent(keyword)}`, { timeout: 20000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(4000);
    const items = await page.evaluate(() =>
      [...document.querySelectorAll('[class*=card], [class*=item], [class*=list]')].slice(0, 4).map(el => {
        const priceText = el.textContent.match(/¥\s*([\d,]+)/);
        return {
          name: el.querySelector('a, h3, [class*=title]')?.textContent?.trim()?.slice(0, 50) || '',
          price: priceText ? parseFloat(priceText[1].replace(/,/g, '')) : 0,
          shop: '闲鱼卖家',
          platform: '闲鱼',
          isUsed: true
        };
      }).filter(x => x.price > 0)
    );
    // Fallback: estimate price range from search result snippets
    if (!items.length) {
      const text = await page.evaluate(() => document.body.innerText.slice(0, 2000));
      const priceMatches = [...text.matchAll(/¥\s*(\d[\d,]*)/g)];
      if (priceMatches.length) {
        const prices = priceMatches.map(m => parseFloat(m[1].replace(/,/g, ''))).filter(p => p > 10);
        if (prices.length) {
          return [{
            name: keyword + ' 闲鱼',
            price: Math.round(prices.reduce((a,b) => a + b, 0) / prices.length),
            shop: '闲鱼均价',
            platform: '闲鱼',
            isUsed: true,
            priceRange: { min: Math.min(...prices), max: Math.max(...prices) }
          }];
        }
      }
    }
    return items;
  } catch(e) { return []; }
  finally { await page.close(); }
}

async function main() {
  const args = process.argv.slice(2);
  const isWeekly = args.includes('--schedule') && args.includes('weekly');
  console.log(`🔍 硬件价格采集 ${isWeekly ? '(周任务)' : '(手动)'} — ${new Date().toLocaleString()}\n`);

  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const result = { updated: new Date().toISOString(), sources: ['京东', '淘宝', '拼多多', '闲鱼', '什么值得买'], parts: {} };

  for (const [cat, products] of Object.entries(PRODUCTS)) {
    console.log(`\n📦 ${cat.toUpperCase()}:`);
    result.parts[cat] = [];

    for (const prod of products.slice(0, isWeekly ? 8 : 5)) {
      const platforms = [];

      // 京东
      try {
        const jd = await searchJD(browser, prod.kw_jd);
        platforms.push(...jd.slice(0, 2));
        if (jd.length) console.log(`  ${prod.name}: JD ${jd.length}条`);
      } catch(e) {}

      // 淘宝
      await new Promise(r => setTimeout(r, 800));
      try {
        const tb = await searchTaobao(prod.kw_tb);
        platforms.push(...tb.slice(0, 2));
        if (tb.length) console.log(`  ${prod.name}: TB ${tb.length}条`);
      } catch(e) {}

      // 拼多多
      await new Promise(r => setTimeout(r, 800));
      try {
        const pdd = await searchPDD(prod.kw_tb);
        platforms.push(...pdd.slice(0, 2));
        if (pdd.length) console.log(`  ${prod.name}: PDD ${pdd.length}条`);
      } catch(e) {}

      // 闲鱼
      await new Promise(r => setTimeout(r, 800));
      try {
        const xy = await searchXianyu(browser, prod.kw_tb);
        platforms.push(...xy.slice(0, 2));
        if (xy.length) console.log(`  ${prod.name}: XY ${xy.length}条`);
      } catch(e) {}

      if (platforms.length) {
        const best = platforms.reduce((min, p) => p.price < min.price ? p : min, platforms[0]);
        result.parts[cat].push({
          name: prod.name,
          platforms: platforms.map(p => ({ platform: p.platform, price: p.price, shop: p.shop || '', url: p.url || '', isUsed: p.isUsed || false })),
          best_price: best.price,
          best_platform: best.platform
        });
      }
    }
  }

  await browser.close();

  // Save
  fs.writeFileSync(OUTPUT, JSON.stringify(result, null, 4), 'utf8');
  console.log(`\n✅ 保存到 ${OUTPUT}`);
  console.log(`   品类: ${Object.keys(result.parts).filter(k => result.parts[k].length).join(', ')}`);
  console.log(`   总计: ${Object.values(result.parts).reduce((s, arr) => s + arr.length, 0)} 个产品`);

  // Archive old prices to price_cache.db
  try {
    const dbPath = DB_PATH;
    const existing = fs.existsSync(dbPath) ? JSON.parse(fs.readFileSync(dbPath, 'utf8')) : { history: [] };
    existing.history.push({ timestamp: new Date().toISOString(), summary: Object.fromEntries(
      Object.entries(result.parts).map(([k, arr]) => [k, arr.length])
    )});
    if (existing.history.length > 52) existing.history = existing.history.slice(-52); // Keep 1 year
    fs.writeFileSync(dbPath, JSON.stringify(existing, null, 2), 'utf8');
    console.log(`   归档: price_cache.db (${existing.history.length} 条记录)`);
  } catch(e) {}

  // Update server cache
  try {
    await fetch('http://localhost:3000/api/prices/all', { method: 'POST', signal: AbortSignal.timeout(5000) });
    console.log('   Server cache refreshed');
  } catch(e) { /* server might not be running */ }
}

main().catch(e => { console.error('❌', e.message); process.exit(1); });
