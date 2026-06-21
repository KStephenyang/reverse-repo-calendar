# 央行逆回购操作日历

在线日历展示中国人民银行每日逆回购操作量和到期量，数据自动更新。

🔗 **演示**: `https://<你的项目>.pages.dev`

## 功能

- 📅 日历视图查看每日逆回购操作量、到期量
- 📊 顶部汇总卡片：今日操作、到期、净投放、最新利率
- 🔄 每日 18:00 CST 自动更新数据
- 📱 移动端响应式适配

## 技术架构

```
GitHub Actions (每天18:00 CST)
  → Python 爬取 PBOC 官网公告
  → 计算到期日 & 聚合数据
  → 输出 data.json
  → 提交到 Git

Cloudflare Pages
  → 监听 Git push
  → 部署静态站点
  → 前端 FullCalendar 渲染日历
```

## 🚀 从零部署指南

### 第一步：创建 GitHub 仓库

1. 登录 [GitHub](https://github.com)，点击右上角 **+** → **New repository**
2. 仓库名填写 `reverse-repo-calendar`，选择 **Public**
3. 不要勾选 "Add a README file"
4. 点击 **Create repository**

### 第二步：推送代码到 GitHub

在项目目录下执行：

```bash
cd reverse-repo-calendar

# 初始化 Git
git init
git add .
git commit -m "feat: PBOC reverse repo calendar"

# 关联远程仓库（替换为你的仓库地址）
git remote add origin https://github.com/<你的用户名>/reverse-repo-calendar.git
git branch -M main
git push -u origin main
```

### 第三步：部署到 Cloudflare Pages

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com/)
2. 左侧菜单 → **Workers & Pages** → **Pages** → **连接到 Git**
3. 授权 GitHub，选择 `reverse-repo-calendar` 仓库
4. 构建设置：
   - **构建命令**: 留空
   - **输出目录**: `docs`
5. 点击 **保存并部署**
6. 等待 1-2 分钟，部署完成后获得 URL：`https://reverse-repo-calendar-xxx.pages.dev`

### 第四步：手动触发首次更新

1. 在 GitHub 仓库页面 → **Actions** → **Daily Data Update**
2. 点击 **Run workflow** → **Run workflow**
3. 等待运行完成（约 2-3 分钟）
4. 刷新 Cloudflare Pages 页面，查看日历数据

### 第五步：（可选）绑定自定义域名

1. Cloudflare Pages → 你的项目 → **自定义域**
2. 添加你的域名，按提示配置 DNS

## 项目结构

```
reverse-repo-calendar/
├── .github/workflows/
│   └── daily-update.yml      # 每日定时更新
├── docs/                     # Cloudflare Pages 部署
│   ├── index.html            # 日历前端
│   └── data.json             # 每日数据（自动更新）
├── fetch_data.py             # 数据抓取脚本
├── requirements.txt
└── README.md
```

## 数据说明

- **数据源**: 中国人民银行公开市场业务交易公告
- **抓取频率**: 每日北京时间 18:00
- **操作量**: 当日央行新开展的逆回购总量（亿元）
- **到期量**: 当日到期的逆回购总量（亿元，按 操作日+期限天数 计算）
- **净投放**: 操作量 - 到期量（正值=净投放，负值=净回笼）

## 本地运行

```bash
pip install -r requirements.txt
python fetch_data.py
# 打开 docs/index.html 查看日历
```
