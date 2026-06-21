# 中国金融市场数据看板

一站式中国/全球金融市场数据看板，涵盖 11 大类金融数据，5 个 Tab 页切换展示，每日自动更新。

## 功能概览

### Tab 1：公开市场
- 📅 FullCalendar 日历视图查看央行逆回购操作量、到期量
- 📊 汇总卡片：操作量、到期量、净投放、最新利率

### Tab 2：汇率利率
- 📈 CNH/CNY 汇率走势
- 💰 中国 LPR（1Y/5Y）& 美国5年期国债收益率

### Tab 3：期货大宗
- 🥇 黄金 Au99.99 实时价格 & 迷你走势图
- 🛢️ 国内原油期货 SC 价格
- 📊 中信期货各品种多空持仓表

### Tab 4：市场情绪
- 🌡️ 沪深300恐惧贪婪温度计（0-100）
- 📋 近期历史情绪表

### Tab 5：宏观经济
- 📉 通胀：中国 CPI/PPI + 美国 CPI
- 🏭 景气度：中国制造业 PMI + 美国 ISM PMI
- 👷 就业：美国非农/失业率 + 中国城镇失业率
- 💵 流动性：M1/M2 + 两融余额

## 数据源

| 类别 | 数据源 | 更新频率 |
|------|--------|----------|
| 央行逆回购 | PBOC 官网公告 | 每日 |
| 汇率 | AKShare (中行折算价 + Eastmoney) | 每日 |
| 期货持仓 | 上期所/中金所/大商所/郑商所 | 每日 |
| 恐惧贪婪 | AKShare (沪深300估值) | 每日 |
| 黄金/原油 | 上海黄金交易所 / 新浪期货 | 每日 |
| LPR / 美国国债 | AKShare (LPR + 中美利差) | 每月/每日 |
| CPI/PPI | AKShare (国家统计局 + 金十数据) | 每月 |
| PMI | AKShare (中国PMI + ISM PMI) | 每月 |
| 就业 | AKShare (非农 + 失业率) | 每月 |
| 两融余额 | AKShare (上交所/深交所) | 每日 |
| M1/M2 | AKShare (央行货币供应量) | 每月 |

## 技术架构

```
GitHub Actions (每天 21:00 CST)
  → Python 脚本抓取 11 类金融数据
  → 每个数据源独立 try/except 降级
  → 输出 docs/data.json
  → 部署 docs/ 到 GitHub Pages
  → 同时提交 data.json 回仓库

GitHub Pages
  → 托管在 gh-pages 分支
  → 静态站点 (HTML + JSON + Chart.js)
  → 每次 Action 运行后自动刷新
```

## 项目结构

```
reverse-repo-calendar/
├── .github/workflows/
│   └── daily-update.yml      # 每日定时更新 (21:00 CST)
├── docs/                     # GitHub Pages 部署目录
│   ├── index.html            # 看板前端 (5 Tab + Chart.js)
│   └── data.json             # 每日数据 (自动更新)
├── fetch_all_data.py         # 全数据抓取脚本 (11个数据源)
├── fetch_data.py             # 逆回购单独脚本 (备用)
├── requirements.txt          # Python 依赖
└── README.md
```

## 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 抓取数据
python fetch_all_data.py

# 启动本地预览
python -m http.server 8080 -d docs
# 打开 http://localhost:8080
```

## 部署到 GitHub Pages

### 首次部署

1. 将代码推送到 GitHub
2. 进入仓库 **Settings** → **Pages**
3. **Source** 选择 **Deploy from a branch**
4. **Branch** 选择 `gh-pages`，文件夹选 `/ (root)` → **Save**
5. 进入 **Actions** → **Daily Data Update & Deploy** → **Run workflow**
6. 等待运行完成，访问 `https://<你的用户名>.github.io/reverse-repo-calendar/`

### 后续更新

- GitHub Actions 每天 21:00 CST 自动运行，无需手动操作
- 也可在 Actions 页面手动触发更新

## 数据说明

- 所有数据均来自公开免费 API（PBOC 官网、AKShare 等）
- 每个数据源独立错误处理，单个源失败不影响其他数据
- 未发布的数据（如尚未公布的CPI）自动回退到最近一期已有数据
- 上游 API 宕机时，对应字段显示 `--` 占位符
