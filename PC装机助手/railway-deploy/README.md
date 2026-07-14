# 将代码推送到 GitHub 仓库，然后 Railway 自动部署

## 方法 A: 新建 GitHub 仓库 → 手动推代码

```bash
# 1. 打开 https://github.com/new 创建新仓库，取名 pc-builder
# 2. 不要勾选任何初始化选项，直接创建

# 3. 在本地终端运行：
cd D:\VSCC\PC装机助手\railway-deploy
git init
git add .
git commit -m "Initial commit - PC Builder完整版"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/pc-builder.git
git push -u origin main
```

## 方法 B: 直接用 Railway CLI

```bash
cd D:\VSCC\PC装机助手\railway-deploy
npx railway login
npx railway init
npx railway up
npx railway domain
```

## Railway 仪表盘设置

创建项目后，在 Railway 中设置：

| 设置 | 值 |
|------|-----|
| Start Command | `node server.js` |
| Build Command | `npx playwright install chromium --with-deps` |
| Root Directory | 留空 |

## 部署完成后

Railway 会给一个 `https://xxx.up.railway.app` 链接，别人直接打开就能用。

## 项目文件

| 文件 | 说明 |
|------|------|
| index.html | 前端页面 (已从 pc-builder.html 复制) |
| server.js | 后端服务 (API + 静态文件) |
| prices.json | 价格数据 |
| refresh_prices.js | 定时采集脚本 |
| package.json | 依赖配置 |