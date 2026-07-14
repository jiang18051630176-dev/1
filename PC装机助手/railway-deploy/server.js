// PC装机助手 — 完整版部署 (Vercel/Netlify Functions 兼容)
// 运行: node server.js → http://localhost:3000
// 部署: 此文件 + index.html + prices.json + refresh_prices.js

const http = require('http');
const fs = require('fs');
const path = require('path');
const PORT = process.env.PORT || 3000;

let scrapeStatus = { running: false, progress: 0, message: '', startTime: null, results: null };
const cache = new Map();
const CACHE_MS = 300000;

const MIME = { '.html':'text/html; charset=utf-8', '.css':'text/css', '.js':'application/javascript', '.json':'application/json', '.ico':'image/x-icon' };

function loadPrices() {
  try { return JSON.parse(fs.readFileSync(path.join(__dirname, 'prices.json'), 'utf8')); }
  catch { return null; }
}

function loadHistory() {
  try {
    const db = JSON.parse(fs.readFileSync(path.join(__dirname, 'price_cache.db'), 'utf8'));
    return db.history || [];
  } catch { return []; }
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  const url = new URL(req.url, 'http://localhost');
  const pn = url.pathname;

  // ===== GET /api/prices — 当前价格 =====
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

  // ===== GET /api/trend — 价格趋势 =====
  if (pn === '/api/trend') {
    const type = url.searchParams.get('type') || 'cpu';
    const name = url.searchParams.get('name') || '';
    const prices = loadPrices();
    const history = loadHistory();
    const trend = [];

    const product = prices?.parts?.[type]?.find(p => p.name.includes(name));
    if (product) {
      trend.push({ date: (prices.updated || '').slice(0,10), price: product.best_price, platform: product.best_platform });
    }

    history.slice(-30).forEach(h => {
      if (h.prices?.[name]) {
        trend.push({ date: h.timestamp.slice(0,10), price: h.prices[name], platform: '' });
      }
    });

    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ type, name, trend }));
    return;
  }

  // ===== POST /api/scrape — 触发采集 =====
  if (pn === '/api/scrape' && req.method === 'POST') {
    if (scrapeStatus.running) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ status: 'already_running' }));
    }
    scrapeStatus = { running: true, progress: 0, message: '启动采集...', startTime: Date.now(), results: null };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'started' }));

    // 异步执行
    (async () => {
      try {
        scrapeStatus.message = '正在采集京东/淘宝/拼多多/闲鱼...';
        const { execSync } = require('child_process');
        const output = execSync('node refresh_prices.js', { cwd: __dirname, timeout: 300000, encoding: 'utf8' });
        const data = loadPrices();
        scrapeStatus.results = data;
        scrapeStatus.progress = 100;
        scrapeStatus.message = '采集完成';
        scrapeStatus.running = false;
        cache.delete('prices_v2');
      } catch(e) {
        scrapeStatus.message = '采集失败: ' + e.message;
        scrapeStatus.running = false;
      }
    })();
    return;
  }

  // ===== GET /api/scrape/status — 采集进度 =====
  if (pn === '/api/scrape/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(scrapeStatus));
    return;
  }

  // ===== 静态文件 =====
  let fp = pn === '/' ? '/index.html' : pn;
  // 兼容旧版 /pc-builder.html
  if (fp === '/pc-builder.html') fp = '/index.html';
  fp = path.join(__dirname, fp);
  try {
    const mimeType = MIME[path.extname(fp)] || 'text/plain';
    res.writeHead(200, { 'Content-Type': mimeType });
    res.end(fs.readFileSync(fp));
  } catch {
    res.writeHead(404); res.end('404 Not Found');
  }
});

server.listen(PORT, () => {
  console.log(`\n🖥️  PC装机助手 — 完整版`);
  console.log(`   http://localhost:${PORT}`);
  console.log(`   /api/prices /api/trend /api/scrape /api/scrape/status\n`);
});