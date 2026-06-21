"""
中国金融市场数据看板 — 全数据抓取脚本 v2
==========================================
每天抓取多类金融数据，输出到 docs/data.json。
使用 AKShare 免费 API，每个数据源独立错误处理。

数据源: AKShare + PBOC 官网
"""

import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Suppress proxy env vars that may interfere with outbound requests
for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(_k, None)

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from bs4 import BeautifulSoup

# ============================================================
# 配置
# ============================================================

OUTPUT_DIR = Path(__file__).resolve().parent / "docs"
OUTPUT_FILE = OUTPUT_DIR / "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

HISTORY_DAYS = 30
HISTORY_MONTHS = 12
CST = timedelta(hours=8)


def clean_date(raw: str) -> str:
    """清洗 DataFrame 中的中文日期格式为 YYYY-MM"""
    if not raw:
        return ""
    s = str(raw)
    # 移除"年"、"月份"、"月"等中文字符
    for ch in ["年", "月份", "月", "日"]:
        s = s.replace(ch, "-" if ch == "年" else "")
    s = s.strip().rstrip("-").strip()
    # 检测是否是乱码（含不可读字符）
    try:
        s.encode("utf-8")
    except Exception:
        return s
    return s


def cst_now():
    return datetime.utcnow() + CST


def today_str():
    return cst_now().strftime("%Y-%m-%d")


def sanitize(obj):
    """递归替换 NaN/Infinity 为 None，避免写出非标准 JSON(浏览器 JSON.parse 无法解析)"""
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def load_existing():
    try:
        if OUTPUT_FILE.exists():
            return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def safe_fetch(name, fn, *args, **kwargs):
    """安全调用 fetch 函数，捕获异常"""
    try:
        print(f"\n{'='*50}")
        print(f"  [{name}] 开始获取...")
        data = fn(*args, **kwargs)
        if data is not None:
            print(f"  [{name}] OK")
            return "ok", data, None
        else:
            print(f"  [{name}] 无数据")
            return "error", None, "无数据返回"
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"  [{name}] FAIL: {msg}")
        traceback.print_exc()
        return "error", None, msg


# ============================================================
# 1. 逆回购
# ============================================================

BASE_URL = "http://www.pbc.gov.cn"
LIST_URL = f"{BASE_URL}/zhengcehuobisi/125207/125213/125431/125475/index.html"


def _fetch_page(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace")


def _get_ids(pages=8):
    ids = []
    for p in range(pages):
        url = LIST_URL if p == 0 else f"{BASE_URL}/zhengcehuobisi/125207/125213/125431/125475/index_{p}.html"
        try:
            html = _fetch_page(url)
            ids.extend(re.findall(r"125475/(\d{19})/index\.html", html))
        except Exception as e:
            print(f"    页{p}失败: {e}")
            break
    seen = set()
    return [x for x in ids if not (x in seen or seen.add(x))]


def _parse(html):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text()
    if "逆回购" not in text:
        return None
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not m:
        return None
    date_str = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    ops = []
    for am, tm in re.findall(r"(\d+)亿元\s*(\d+)天期\s*逆回购", text):
        rm = re.search(r"(\d+\.?\d*)%", text)
        ops.append({"term": int(tm), "amount": int(am), "rate": float(rm.group(1)) if rm else 0.0})
    return {"date": date_str, "operations": ops} if ops else None


def fetch_reverse_repo(existing=None):
    old_recs = (existing or {}).get("reverse_repo", {}).get("records", [])
    ids = _get_ids(8)
    print(f"    {len(ids)} 条公告ID")

    records = []
    ok = skip = 0
    for i, aid in enumerate(ids):
        try:
            html = _fetch_page(f"{BASE_URL}/zhengcehuobisi/125207/125213/125431/125475/{aid}/index.html")
            r = _parse(html)
            if not r:
                skip += 1
                continue
            records.append({
                "date": r["date"],
                "term": r["operations"][0]["term"],
                "total_amount": sum(o["amount"] for o in r["operations"]),
                "rate": r["operations"][0]["rate"],
                "ops_detail": r["operations"],
            })
            ok += 1
            if (i + 1) % 30 == 0:
                print(f"    进度: {i+1}/{len(ids)} ({ok} ok, {skip} skip)")
        except Exception:
            skip += 1
        if (i + 1) % 5 == 0:
            time.sleep(0.15)

    # merge old records not in new
    dates = {r["date"] for r in records}
    for r in old_recs:
        if r["date"] not in dates:
            records.append(r)
    records.sort(key=lambda x: x["date"], reverse=True)

    # calculate maturities
    from collections import defaultdict
    daily = defaultdict(lambda: {"operation": 0, "maturity": 0})
    for rec in records:
        daily[rec["date"]]["operation"] += rec["total_amount"]
        dt = datetime.strptime(rec["date"], "%Y-%m-%d") + timedelta(days=rec["term"])
        daily[dt.strftime("%Y-%m-%d")]["maturity"] += rec["total_amount"]
    for d in daily.values():
        d["net"] = d["operation"] - d["maturity"]

    print(f"    {len(records)} 条记录, {len(daily)} 天")
    return {"records": records, "daily": dict(sorted(daily.items()))}


# ============================================================
# 2. 汇率
# ============================================================

def fetch_forex(existing=None):
    import akshare as ak

    # CNH via forex_spot_em — columns: [序号, 代码, 名称, 最新价, 涨跌额, 涨跌幅, 昨收, ...]
    cnh = {"price": None, "change_pct": 0}
    try:
        df = ak.forex_spot_em()
        # Search by column name (position-independent)
        name_col = None
        price_col = None
        chg_col = None
        for c in df.columns:
            cs = str(c)
            if "名称" in cs:
                name_col = c
            elif "最新价" in cs:
                price_col = c
            elif "涨跌幅" in cs:
                chg_col = c
        if name_col and price_col:
            for _, row in df.iterrows():
                n = str(row[name_col])
                if "离岸" in n or "CNH" in n.upper():
                    raw = row[price_col]
                    if raw is not None:
                        cnh["price"] = float(raw) / 10000  # Eastmoney scales forex by 10000
                    if chg_col and row[chg_col] is not None:
                        cnh["change_pct"] = float(row[chg_col])
                    break
    except Exception as e:
        print(f"    CNH (AKShare): {e}")

    # CNH history via forex_hist_em — real daily K-line series (date, open, close, high, low, ...)
    cnh_history = []
    try:
        hdf = ak.forex_hist_em(symbol="USDCNH")
        if hdf is not None and len(hdf) > 0:
            cols = hdf.columns.tolist()
            date_col = cols[0]
            close_col = next((c for c in cols if "收盘" in str(c)), None) or (cols[2] if len(cols) > 2 else cols[-1])
            hdf = hdf.tail(HISTORY_DAYS)
            for _, row in hdf.iterrows():
                try:
                    cnh_history.append({"date": str(row[date_col])[:10], "price": float(row[close_col])})
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        print(f"    CNH history: {e}")

    # frankfurter.app fallback session — AKShare's Eastmoney/safe.gov.cn sources are
    # domestic-China endpoints that frequently fail from GitHub Actions' overseas IPs
    # even though they work fine on a local network, so this covers both the price
    # and the history when that happens.
    def _frankfurter_latest():
        s = requests.Session()
        s.trust_env = False
        r = s.get("https://api.frankfurter.app/latest?from=USD&to=CNY", timeout=10)
        val = r.json().get("rates", {}).get("CNY")
        return round(float(val), 4) if val else None

    def _frankfurter_history(days):
        s = requests.Session()
        s.trust_env = False
        end = datetime.now()
        start = end - timedelta(days=int(days * 1.6) + 5)  # pad for weekends/holidays
        url = f"https://api.frankfurter.app/{start:%Y-%m-%d}..{end:%Y-%m-%d}?from=USD&to=CNY"
        r = s.get(url, timeout=10)
        rates = r.json().get("rates", {})
        out = [{"date": d, "price": round(float(v["CNY"]), 4)} for d, v in sorted(rates.items()) if "CNY" in v]
        return out[-days:]

    # CNH fallback: free FX API (frankfurter.app — uses ECB rates, close to CNH)
    if cnh["price"] is None:
        try:
            val = _frankfurter_latest()
            if val:
                cnh["price"] = val
                cnh["change_pct"] = 0  # no change% from this source
                print("    CNH from frankfurter.app")
        except Exception as e2:
            print(f"    CNH fallback: {e2}")

    # CNH history fallback: AKShare's forex_hist_em hits Eastmoney, which often fails in CI
    if not cnh_history:
        try:
            cnh_history = _frankfurter_history(HISTORY_DAYS)
            if cnh_history:
                print("    CNH history from frankfurter.app")
        except Exception as e2:
            print(f"    CNH history fallback: {e2}")

    # CNY via currency_boc_safe — columns are currency names (e.g. "美元"), rows are dates
    cny = {"price": None, "change_pct": 0}
    try:
        df = ak.currency_boc_safe()
        # Find the USD column by searching column names
        usd_col = None
        for c in df.columns:
            if "美元" in str(c):
                usd_col = c
                break
        if usd_col is not None and len(df) > 0:
            # Latest row (DataFrame is oldest-first, so last row is most recent)
            latest_val = float(df.iloc[-1][usd_col])
            cny["price"] = latest_val / 100  # Bank of China: 100 USD = X RMB
    except Exception as e:
        print(f"    CNY: {e}")

    # CNY fallback: currency_boc_safe scrapes safe.gov.cn (3 sequential requests), which
    # frequently fails from CI; approximate with frankfurter.app's interbank rate instead
    if cny["price"] is None:
        try:
            val = cnh["price"] if cnh["price"] is not None else _frankfurter_latest()
            if val:
                cny["price"] = val
                print("    CNY from frankfurter.app (approx.)")
        except Exception as e2:
            print(f"    CNY fallback: {e2}")

    # Preserve previous history if today's fetch failed (keeps chart populated)
    if not cnh_history and existing:
        cnh_history = existing.get("forex", {}).get("cnh", {}).get("history", [])

    return {"cnh": {"latest": cnh, "history": cnh_history, "date": today_str()},
            "cny": {"latest": cny, "date": today_str()}}


# ============================================================
# 3. 期货持仓（中信期货）
# ============================================================

def _futures_extract(fn, name, date_str):
    """通用提取：从交易所持仓表筛选持仓数据"""
    try:
        df = fn(date=date_str)
    except Exception:
        # 尝试前几个交易日
        dt = datetime.strptime(date_str, "%Y%m%d")
        for _ in range(5):
            dt -= timedelta(days=1)
            try:
                df = fn(date=dt.strftime("%Y%m%d"))
                break
            except Exception:
                continue
        else:
            return {"date": None, "products": []}

    if df is None or len(df) == 0:
        return {"date": date_str, "products": []}

    cols = df.columns.tolist()
    # 查找关键列
    mem_col = next((c for c in cols if "会员" in c or "part" in c.lower()), None)
    prod_col = next((c for c in cols if "品种" in c or "symbol" in c.lower() or "var" in c.lower()), None)
    long_col = next((c for c in cols if "持买" in c or "long_hld" in c.lower()), None)
    long_chg_col = next((c for c in cols if "买变化" in c or "long_chg" in c.lower()), None)
    short_col = next((c for c in cols if "持卖" in c or "short_hld" in c.lower()), None)
    short_chg_col = next((c for c in cols if "卖变化" in c or "short_chg" in c.lower()), None)

    if not mem_col:
        return {"date": date_str, "products": []}

    zx = df[df[mem_col].astype(str).str.contains("中信", na=False)]
    products = []
    for _, row in zx.iterrows():
        lv = int(row[long_col]) if long_col else 0
        sv = int(row[short_col]) if short_col else 0
        products.append({
            "member": str(row[mem_col]),
            "product": str(row[prod_col]) if prod_col else "",
            "long_vol": lv,
            "long_chg": int(row[long_chg_col]) if long_chg_col else 0,
            "short_vol": sv,
            "short_chg": int(row[short_chg_col]) if short_chg_col else 0,
            "net": lv - sv,
        })
    print(f"    {name}: {len(products)} 条")
    return {"date": date_str, "products": products}


def fetch_futures(existing=None):
    import akshare as ak
    d = cst_now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    ds = d.strftime("%Y%m%d")

    result = {}
    result["shfe"] = _futures_extract(ak.get_shfe_rank_table, "SHFE", ds)
    result["cffex"] = _futures_extract(ak.get_cffex_rank_table, "CFFEX", ds)
    try:
        result["dce"] = _futures_extract(ak.get_dce_rank_table, "DCE", ds)
    except Exception:
        result["dce"] = {"date": None, "products": []}
    try:
        result["czce"] = _futures_extract(ak.get_czce_rank_table, "CZCE", ds)
    except Exception:
        result["czce"] = {"date": None, "products": []}
    return result


# ============================================================
# 4. 市场情绪 - 使用简化版：沪深300指数涨跌+振幅作为替代情绪指标
# ============================================================

def fetch_sentiment(existing=None):
    """使用沪深300指数技术指标作为情绪参考"""
    import akshare as ak
    try:
        df = ak.stock_market_pe_lg(symbol="沪深300")
        # Columns: 日期, 总市值, 盈利(近似PE参考)
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            # col2 (index 2) = 盈利值, used as PE-like reference (typical range 80-120)
            pe_ref = float(latest.iloc[2]) if len(latest) > 2 else 100
            # 基于PE参考值百分位生成情绪值 (0-100)
            # pe_ref around 80-90 = fear zone, 100 = neutral, 110+ = greed
            sentiment_val = max(0, min(100, (pe_ref - 80) * 2.5))
            status = "中性"
            if sentiment_val <= 25:
                status = "极度恐惧"
            elif sentiment_val <= 45:
                status = "恐惧"
            elif sentiment_val <= 55:
                status = "中性"
            elif sentiment_val <= 75:
                status = "贪婪"
            else:
                status = "极度贪婪"
            return {
                "latest": {"date": str(latest.iloc[0])[:10] if len(latest) > 0 else today_str(),
                           "value": round(sentiment_val, 1), "status": status, "note": "基于沪深300估值推算"},
                "history": [],
            }
    except Exception as e:
        print(f"    情绪指标失败: {e}")

    # Fallback: return a neutral placeholder
    return {
        "latest": {"date": today_str(), "value": 50.0, "status": "中性", "note": "API暂不可用，显示默认值"},
        "history": [],
    }


# ============================================================
# 5. 大宗商品
# ============================================================

def fetch_commodities(existing=None):
    import akshare as ak
    result = {}

    # 黄金
    try:
        df = ak.spot_hist_sge(symbol="Au99.99")
        if df is not None and len(df) > 0:
            df = df.sort_values("date", ascending=False)
            l, p = df.iloc[0], df.iloc[1] if len(df) > 1 else df.iloc[0]
            price = float(l["close"])
            pct = (price - float(p["close"])) / float(p["close"]) * 100 if len(df) > 1 else 0
            result["gold"] = {
                "latest": {"date": str(l["date"])[:10], "price": price, "change_pct": round(pct, 2)},
                "history": [{"date": str(r["date"])[:10], "price": float(r["close"])}
                            for _, r in df.head(HISTORY_DAYS).iterrows()],
            }
        else:
            result["gold"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    黄金: {e}")
        result["gold"] = {"latest": None, "history": []}

    # 原油 - 使用国内原油期货 (SC主力合约 via futures_main_sina)
    try:
        end_d = cst_now().strftime("%Y%m%d")
        start_d = (cst_now() - timedelta(days=30)).strftime("%Y%m%d")
        df = ak.futures_main_sina(symbol="SC0", start_date=start_d, end_date=end_d)
        if df is not None and len(df) > 0:
            # Cols: 日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 持仓量, 动态结算价
            cols = df.columns.tolist()
            l = df.iloc[-1]
            p = df.iloc[-2] if len(df) > 1 else l
            price = float(l.iloc[4]) if len(l) > 4 else 0
            prev_price = float(p.iloc[4]) if len(p) > 4 else price
            pct = (price - prev_price) / prev_price * 100 if prev_price > 0 else 0
            result["oil_cn"] = {
                "latest": {"date": str(l.iloc[0])[:10], "price": round(price, 1), "change_pct": round(pct, 2)},
            }
        else:
            result["oil_cn"] = {"latest": None}
    except Exception as e:
        print(f"    原油: {e}")
        result["oil_cn"] = {"latest": None}

    return result


# ============================================================
# 6. 利率
# ============================================================

def fetch_interest_rates(existing=None):
    import akshare as ak
    import pandas as pd
    result = {}

    # 中国 LPR
    try:
        df = ak.macro_china_lpr()
        if df is not None and len(df) > 0:
            # Column names are Chinese, access by position
            cols = df.columns.tolist()
            # Typically: TRADE_DATE, LPR1Y, LPR5Y, RATE_1, RATE_2
            df = df.sort_values(cols[0], ascending=False)
            l = df.iloc[0]
            result["china_lpr"] = {
                "latest": {
                    "date": str(l[cols[0]])[:10],
                    "lpr_1y": float(l["LPR1Y"]) if "LPR1Y" in cols else float(l.iloc[1]) if len(l) > 1 else None,
                    "lpr_5y": float(l["LPR5Y"]) if "LPR5Y" in cols else float(l.iloc[2]) if len(l) > 2 else None,
                },
                "history": [],
            }
        else:
            result["china_lpr"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    LPR: {e}")
        result["china_lpr"] = {"latest": None, "history": []}

    # 美国5年期国债 - bond_zh_us_rate has col "美国国债收益率5年"
    try:
        df = ak.bond_zh_us_rate()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            # Find the US 5Y yield column (contains "美国" and "5")
            col_5y = next((c for c in cols if "美国" in str(c) and "5" in str(c)), None)
            date_col = cols[0]  # first column is date
            if col_5y:
                df = df.sort_values(date_col, ascending=False)
                l = df.iloc[0]
                val = l[col_5y]
                import math
                if val is not None and not (isinstance(val, float) and math.isnan(val)):
                    result["us_treasury_5y"] = {
                        "latest": {"date": str(l[date_col])[:10], "yield": float(val)},
                    }
                else:
                    result["us_treasury_5y"] = {"latest": None}
            else:
                result["us_treasury_5y"] = {"latest": None}
        else:
            result["us_treasury_5y"] = {"latest": None}
    except Exception as e:
        print(f"    美国国债: {e}")
        result["us_treasury_5y"] = {"latest": None}

    return result


# ============================================================
# 7. 通胀数据
# ============================================================

def fetch_inflation(existing=None):
    import akshare as ak
    result = {}

    # 中国 CPI - columns: 月份, 全国-当月, 全国-同比增长, 全国-环比增长, 全国-累计, ...
    try:
        df = ak.macro_china_cpi()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            # 月份 is col0, 全国-当月 is col1, 全国-同比增长 is col2, 全国-环比增长 is col3
            result["china_cpi"] = {
                "latest": {
                    "date": clean_date(df.iloc[0, 0]),
                    "cpi_val": float(df.iloc[0, 1]),
                    "cpi_yoy": float(df.iloc[0, 2]),
                    "cpi_mom": float(df.iloc[0, 3]),
                },
                "history": [{"date": clean_date(df.iloc[i, 0]), "cpi_yoy": float(df.iloc[i, 2])}
                            for i in range(min(HISTORY_MONTHS, len(df)))],
            }
        else:
            result["china_cpi"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    CPI: {e}")
        result["china_cpi"] = {"latest": None, "history": []}

    # 中国 PPI - columns: 月份, 当月, 当月同比增长, 累计
    try:
        df = ak.macro_china_ppi()
        if df is not None and len(df) > 0:
            result["china_ppi"] = {
                "latest": {
                    "date": clean_date(df.iloc[0, 0]),
                    "ppi_val": float(df.iloc[0, 1]),
                    "ppi_yoy": float(df.iloc[0, 2]),
                },
                "history": [{"date": clean_date(df.iloc[i, 0]), "ppi_yoy": float(df.iloc[i, 2])}
                            for i in range(min(HISTORY_MONTHS, len(df)))],
            }
        else:
            result["china_ppi"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    PPI: {e}")
        result["china_ppi"] = {"latest": None, "history": []}

    # 美国 CPI - columns: 时间, 发布日期, 现值, 前值
    try:
        df = ak.macro_usa_cpi_yoy()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            df = df.sort_values(cols[0], ascending=False)
            # cols[2] = 现值 (actual CPI YoY value); cols[1] = 发布日期 (release date)
            # Fall back to previous row if latest has NaN (unreleased data)
            import math
            found = None
            for i in range(len(df)):
                raw_val = df.iloc[i][cols[2]]
                if raw_val is not None and not (isinstance(raw_val, float) and math.isnan(raw_val)):
                    found = df.iloc[i]
                    break
            if found is not None:
                result["us_cpi"] = {
                    "latest": {"date": str(found[cols[0]])[:10], "cpi_yoy": float(found[cols[2]])},
                    "history": [],
                }
            else:
                result["us_cpi"] = {"latest": None, "history": []}
        else:
            result["us_cpi"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    US CPI: {e}")
        result["us_cpi"] = {"latest": None, "history": []}

    return result


# ============================================================
# 8. PMI
# ============================================================

def fetch_pmi(existing=None):
    import akshare as ak
    result = {}

    # 中国 PMI - columns: 月份, 制造业-指数, 制造业-同比增长, 非制造业-指数, 非制造业-同比增长
    try:
        df = ak.macro_china_pmi()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            result["china_mfg"] = {
                "latest": {"date": clean_date(df.iloc[0, 0]), "value": float(df.iloc[0, 1])},
                "history": [{"date": clean_date(df.iloc[i, 0]), "value": float(df.iloc[i, 1])}
                            for i in range(min(HISTORY_MONTHS, len(df)))],
            }
        else:
            result["china_mfg"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    China PMI: {e}")
        result["china_mfg"] = {"latest": None, "history": []}

    # 美国 PMI — macro_usa_ism_pmi returns true ISM data (产品, 时间, 现值, 预测值, 前值)
    result["us_ism"] = {"latest": None, "history": []}
    try:
        df = ak.macro_usa_ism_pmi()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            # cols[1] = 时间 (date), cols[2] = 现值 (actual value)
            date_col = cols[1]
            val_col = cols[2]
            df = df.sort_values(date_col, ascending=False)
            l = df.iloc[0]
            raw_val = l[val_col]
            import math
            if raw_val is not None and not (isinstance(raw_val, float) and math.isnan(raw_val)):
                result["us_ism"] = {
                    "latest": {"date": str(l[date_col])[:10], "value": float(raw_val)},
                    "history": [{"date": str(r[date_col])[:10], "value": float(r[val_col])}
                                for _, r in df.head(HISTORY_MONTHS).iterrows()
                                if r[val_col] is not None and not (isinstance(r[val_col], float) and math.isnan(r[val_col]))],
                }
    except Exception as e:
        print(f"    US ISM PMI: {e}")
        # Try Markit PMI as fallback
        try:
            df = ak.macro_usa_pmi()
            if df is not None and len(df) > 0:
                cols = df.columns.tolist()
                date_col = cols[1]
                val_col = cols[2]
                df = df.sort_values(date_col, ascending=False)
                l = df.iloc[0]
                raw_val = l[val_col]
                import math
                if raw_val is not None and not (isinstance(raw_val, float) and math.isnan(raw_val)):
                    result["us_ism"] = {
                        "latest": {"date": str(l[date_col])[:10], "value": float(raw_val)},
                        "history": [],
                    }
        except Exception:
            pass

    return result


# ============================================================
# 9. 就业
# ============================================================

def fetch_employment(existing=None):
    import akshare as ak
    result = {}

    # 中国失业率 — real columns: [date, item, value]; upstream API may be unstable
    try:
        df = ak.macro_china_urban_unemployment()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            # cols[0]=date, cols[1]=item(label), cols[2]=value
            # Filter to the unemployment-rate series if multiple items present
            if "item" in cols:
                df = df[df["item"].astype(str).str.contains("失业", na=False)]
            if len(df) > 0:
                df = df.sort_values(cols[0], ascending=False)
                l = df.iloc[0]
                result["china_unemployment"] = {
                    "latest": {"date": str(l[cols[0]])[:10], "value": float(l[cols[2]]) if len(l) > 2 else None},
                    "history": [],
                }
            else:
                result["china_unemployment"] = {"latest": None, "history": []}
        else:
            result["china_unemployment"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    China unemployment: {e}")
        result["china_unemployment"] = {"latest": None, "history": []}

    # 美国非农 - columns: 产品, 时间, 现值, 预测值, 前值 (filter 产品=="美国非农就业人数")
    try:
        df = ak.macro_usa_non_farm()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            prod_col = cols[0]
            time_col = cols[1]
            val_col = cols[2]
            nf = df[df[prod_col].astype(str).str.contains("非农", na=False)]
            if len(nf) > 0:
                nf = nf.sort_values(time_col, ascending=False)
                # Fall back to previous row if latest has NaN (unreleased)
                import math
                found_row = None
                for i in range(len(nf)):
                    raw = nf.iloc[i][val_col]
                    if raw is not None and not (isinstance(raw, float) and math.isnan(raw)):
                        found_row = nf.iloc[i]
                        break
                if found_row is not None:
                    val_str = str(found_row[val_col]).replace(",", "")
                    try:
                        val = float(val_str)
                    except ValueError:
                        val = None
                    result["us_nonfarm"] = {
                        "latest": {"date": str(found_row[time_col])[:10], "value": val},
                        "history": [],
                    }
                else:
                    result["us_nonfarm"] = {"latest": None, "history": []}
            else:
                result["us_nonfarm"] = {"latest": None, "history": []}
        else:
            result["us_nonfarm"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    US nonfarm: {e}")
        result["us_nonfarm"] = {"latest": None, "history": []}

    # 美国失业率 - columns: 产品, 时间, 现值, 预测值, 前值
    try:
        df = ak.macro_usa_unemployment_rate()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            df = df.sort_values(cols[1], ascending=False)
            # Fall back to previous row if latest has NaN (unreleased)
            import math
            found_row = None
            for i in range(len(df)):
                raw = df.iloc[i][cols[2]]
                if raw is not None and not (isinstance(raw, float) and math.isnan(raw)):
                    found_row = df.iloc[i]
                    break
            if found_row is not None:
                result["us_unemployment_rate"] = {
                    "latest": {"date": str(found_row[cols[1]])[:10], "value": float(found_row[cols[2]])},
                    "history": [],
                }
            else:
                result["us_unemployment_rate"] = {"latest": None, "history": []}
        else:
            result["us_unemployment_rate"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    US unemployment: {e}")
        result["us_unemployment_rate"] = {"latest": None, "history": []}

    return result


# ============================================================
# 10. 两融余额
# ============================================================

def fetch_margin(existing=None):
    import akshare as ak
    result = {}

    # 上交所
    try:
        df = ak.stock_margin_sse(start_date=(cst_now() - timedelta(days=90)).strftime("%Y%m%d"),
                                 end_date=cst_now().strftime("%Y%m%d"))
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            # cols: 信用交易日期, 融资余额, 融资买入额, 融券余量, 融券余量金额, 融券卖出量, 融资融券余额
            l = df.iloc[0]
            result["sse"] = {
                "latest": {
                    "date": str(l[cols[0]]),
                    "rzye": float(l[cols[1]]) if len(l) > 1 else 0,
                    "rzmre": float(l[cols[2]]) if len(l) > 2 else 0,
                    "rqye": float(l[cols[3]]) if len(l) > 3 else 0,
                    "rzrqye": float(l[cols[6]]) if len(l) > 6 else 0,
                },
                "history": [{"date": str(df.iloc[i, 0]), "rzye": float(df.iloc[i, 1]),
                             "rzrqye": float(df.iloc[i, 6]) if len(df.iloc[i]) > 6 else 0}
                            for i in range(min(HISTORY_DAYS, len(df)))],
            }
        else:
            result["sse"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    SSE margin: {e}")
        result["sse"] = {"latest": None, "history": []}

    # 深交所 - different API format (no date param, returns latest summary)
    try:
        df = ak.stock_margin_szse()
        if df is not None and len(df) > 0:
            # columns: 融资买入额, 融资余额, 融券卖出量, 融券余量, 融券余额, 融资融券余额
            l = df.iloc[0]
            result["szse"] = {
                "latest": {
                    "date": cst_now().strftime("%Y%m%d"),
                    "rzye": float(l.iloc[1]) if len(l) > 1 else 0,
                    "rzmre": float(l.iloc[0]) if len(l) > 0 else 0,
                    "rqye": float(l.iloc[3]) if len(l) > 3 else 0,
                    "rzrqye": float(l.iloc[5]) if len(l) > 5 else 0,
                },
                "history": [],
            }
        else:
            result["szse"] = {"latest": None, "history": []}
    except Exception as e:
        print(f"    SZSE margin: {e}")
        result["szse"] = {"latest": None, "history": []}

    return result


# ============================================================
# 11. M1/M2 货币供应量
# ============================================================

def fetch_money_supply(existing=None):
    import akshare as ak
    try:
        df = ak.macro_china_money_supply()
        if df is not None and len(df) > 0:
            cols = df.columns.tolist()
            # cols: 月份, M2-数量(亿元), M2-同比增长, M2-环比增长, M1-数量(亿元), M1-同比增长, ...
            l = df.iloc[0]
            # cols: 月份, M2数量, M2同比, M2环比, M1数量, M1同比, M1环比, M0数量, M0同比, M0环比
            return {
                "latest": {
                    "date": str(l[cols[0]]).replace("年", "-").replace("月份", "").replace("月", "").strip(),
                    "m2_val": float(l[cols[1]]) if len(l) > 1 else None,
                    "m2_yoy": float(l[cols[2]]) if len(l) > 2 else None,
                    "m1_val": float(l[cols[4]]) if len(l) > 4 else None,
                    "m1_yoy": float(l[cols[5]]) if len(l) > 5 else None,
                },
                "history": [],
            }
    except Exception as e:
        print(f"    Money supply: {e}")
    return None


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  金融市场数据看板 - 全数据抓取 v2")
    print(f"  运行: {cst_now().strftime('%Y-%m-%d %H:%M:%S')} CST")
    print("=" * 60)

    existing = load_existing()
    print(f"\n已有数据: {'是' if existing else '否'}")

    output = {"updated_at": cst_now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
              "fetch_status": {}, "fetch_errors": {}}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 定义所有数据源
    sources = [
        ("reverse_repo", fetch_reverse_repo),
        ("forex", fetch_forex),
        ("futures", fetch_futures),
        ("sentiment", fetch_sentiment),
        ("commodities", fetch_commodities),
        ("rates", fetch_interest_rates),
        ("inflation", fetch_inflation),
        ("pmi", fetch_pmi),
        ("employment", fetch_employment),
        ("margin", fetch_margin),
        ("money_supply", fetch_money_supply),
    ]

    for key, fn in sources:
        status, data, err = safe_fetch(key, fn, existing)
        output["fetch_status"][key] = status
        if status == "ok" and data is not None:
            output[key] = data
        elif existing.get(key):
            output[key] = existing[key]
            if err:
                output["fetch_errors"][key] = err
        else:
            output[key] = data if data is not None else {}
            if err:
                output["fetch_errors"][key] = err

    # 写入
    print(f"\n{'='*60}")
    print("  写入输出...")
    output = sanitize(output)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    sz = OUTPUT_FILE.stat().st_size
    print(f"  文件: {OUTPUT_FILE} ({sz:,} bytes)")

    ok = sum(1 for v in output["fetch_status"].values() if v == "ok")
    total = len(output["fetch_status"])
    print(f"\n  状态: {ok}/{total} 成功")
    if output["fetch_errors"]:
        print("  失败:")
        for k, v in output["fetch_errors"].items():
            print(f"    - {k}: {v}")
    else:
        print("  全部成功!")

    # 摘要
    rr = output.get("reverse_repo", {})
    if rr.get("records"):
        td = rr["daily"].get(today_str(), {"operation": 0, "maturity": 0, "net": 0})
        print(f"\n  逆回购: 今日操作 {td['operation']}亿 / 到期 {td['maturity']}亿 / 净投放 {td['net']:+d}亿")

    fx = output.get("forex", {})
    cnh = fx.get("cnh", {}).get("latest", {})
    if cnh.get("price"):
        print(f"  CNH: {cnh['price']:.4f}")

    comm = output.get("commodities", {})
    g = comm.get("gold", {}).get("latest")
    if g:
        print(f"  黄金: {g['price']:.2f} 元/克")

    rt = output.get("rates", {})
    lpr = rt.get("china_lpr", {}).get("latest")
    if lpr:
        print(f"  LPR: 1Y={lpr.get('lpr_1y')}% 5Y={lpr.get('lpr_5y')}%")

    inf = output.get("inflation", {})
    cpi = inf.get("china_cpi", {}).get("latest")
    if cpi:
        print(f"  CPI: {cpi.get('cpi_yoy')}% YoY")

    pmi = output.get("pmi", {}).get("china_mfg", {}).get("latest")
    if pmi:
        print(f"  PMI: {pmi.get('value')}")

    ms = output.get("money_supply", {}).get("latest")
    if ms:
        print(f"  M2: {ms.get('m2_val')} ({ms.get('m2_yoy')}%)")

    mg = output.get("margin", {})
    sse_m = mg.get("sse", {}).get("latest")
    if sse_m and sse_m.get("rzrqye"):
        print(f"  两融(沪): {sse_m['rzrqye']:.0f}亿元")

    print("=" * 60)


if __name__ == "__main__":
    main()
