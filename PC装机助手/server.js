// PC装机助手 — 实时价格服务器 v2
// 端点: /api/prices /api/trend /api/scrape /api/scrape/status
// 运行: node server.js → http://localhost:3000

const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const PORT = 3000;

let scrapeStatus = { running: false, progress: 0, message: '', startTime: null };
const cache = new Map();
const CACHE_MS = 300000;

// 读取 prices.json
function loadPrices() {
  try { return JSON.parse(fs.readFileSync(path.join(__dirname, 'prices.json'), 'utf8')); }
  catch { return null; }
}

// 读取 price_cache.db 历史
function loadHistory() {
  try {
    const db = JSON.parse(fs.readFileSync(path.join(__dirname, 'price_cache.db'), 'utf8'));
    return db.history || [];
  } catch { return []; }
}

// 生成趋势数据
function getTrendData(type, name) {
  const prices = loadPrices();
  if (!prices?.parts?.[type]) return [];

  const history = loadHistory();
  const trend = [];

  // Current data point
  const product = prices.parts[type].find(p => p.name.includes(name));
  if (product) {
    trend.push({ date: prices.updated.slice(0, 10), price: product.best_price, platform: product.best_platform });
  }

  // Historical data from cache
  history.slice(-30).forEach(h => {
    if (h.prices?.[name]) {
      trend.push({ date: h.timestamp.slice(0, 10), price: h.prices[name], platform: '' });
    }
  });

  return trend;
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  const url = new URL(req.url, 'http://localhost');
  const pn = url.pathname;

  // ===== GET /api/prices — 返回当前价格 =====
  if (pn === '/api/prices' || pn === '/api/prices/all') {
    const ck = 'prices_v2';
    const c = cache.get(ck);
    if (c && Date.now() - c.ts < CACHE_MS && req.method !== 'POST') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ cached: true, ...c.data }));
    }
    const data = loadPrices() || {};
    cache.set(ck, { ts: Date.now(), data });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(data));
    return;
  }

  // ===== GET /api/trend?type=cpu&name=R5 — 价格趋势 =====
  if (pn === '/api/trend') {
    const type = url.searchParams.get('type') || 'cpu';
    const name = url.searchParams.get('name') || '';
    const trend = getTrendData(type, name);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ type, name, trend }));
    return;
  }

  // ===== POST /api/scrape — 触发全量抓取 =====
  if (pn === '/api/scrape' && req.method === 'POST') {
    if (scrapeStatus.running) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ status: 'already_running', progress: scrapeStatus.progress }));
    }
    scrapeStatus = { running: true, progress: 0, message: '启动采集...', startTime: Date.now() };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'started' }));

    // 异步执行抓取
    (async () => {
      try {
        const { execSync } = require('child_process');
        scrapeStatus.message = '正在采集京东/淘宝/拼多多/闲鱼...';
        execSync('node refresh_prices.js', { cwd: __dirname, timeout: 300000 });
        scrapeStatus.progress = 100;
        scrapeStatus.message = '采集完成';
        scrapeStatus.running = false;
        cache.delete('prices_v2'); // Invalidate cache
      } catch(e) {
        scrapeStatus.message = '采集失败: ' + e.message;
        scrapeStatus.running = false;
      }
    })();
    return;
  }

  // ===== GET /api/scrape/status — 抓取进度 =====
  if (pn === '/api/scrape/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(scrapeStatus));
    return;
  }

  // ===== 静态文件 =====
  let fp = req.url === '/' ? '/pc-builder.html' : decodeURIComponent(url.pathname);
  fp = path.join(__dirname, path.basename(fp));
  try {
    const mime = { '.html':'text/html; charset=utf-8','.css':'text/css','.js':'application/javascript','.json':'application/json' };
    res.writeHead(200, { 'Content-Type': mime[path.extname(fp)] || 'text/plain' });
    res.end(fs.readFileSync(fp));
  } catch { res.writeHead(404); res.end('404'); }
});

server.listen(PORT, () => {
  console.log(`\n🖥️  PC装机助手 v2 — http://localhost:${PORT}`);
  console.log(`   /api/prices       — 当前价格`);
  console.log(`   /api/trend        — 价格趋势`);
  console.log(`   /api/scrape       — 触发采集 (POST)`);
  console.log(`   /api/scrape/status — 采集进度\n`);
});
