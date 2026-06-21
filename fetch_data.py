"""
央行逆回购数据抓取脚本
=====================
从中国人民银行官网抓取公开市场操作数据，计算每日到期量。

数据源: http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/
每笔逆回购操作: 操作日 + 期限(天) = 到期日

输出: docs/data.json
{
  "records": [
    {"date": "2026-06-18", "operation_amount": 2480, "maturity_amount": 500, "rate": 1.40, "term": 7},
    ...
  ],
  "daily": {
    "2026-06-18": {"operation": 2480, "maturity": 500, "net": 1980},
    ...
  }
}
"""

import json
import re
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ============================================================
# 配置
# ============================================================

BASE_URL = "http://www.pbc.gov.cn"
LIST_URL = f"{BASE_URL}/zhengcehuobisi/125207/125213/125431/125475/index.html"
OUTPUT_DIR = Path(__file__).resolve().parent / "docs"
OUTPUT_FILE = OUTPUT_DIR / "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def fetch_page(url: str) -> str:
    """获取网页内容（UTF-8 编码）"""
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    # 从 raw bytes 强制 UTF-8 解码（PBOC 网站 meta 标注 utf-8）
    return r.content.decode("utf-8", errors="replace")


def get_announcement_ids(pages: int = 15) -> list:
    """
    从列表页获取公告 ID 列表。
    每页约 15 条公告，默认拉 15 页（约 225 条，覆盖约 1 年）。
    """
    ids = []
    for page_num in range(pages):
        if page_num == 0:
            url = LIST_URL
        else:
            url = f"{BASE_URL}/zhengcehuobisi/125207/125213/125431/125475/index_{page_num}.html"

        try:
            html = fetch_page(url)
            # 查找公告详情页链接 ID
            found = re.findall(r"125475/(\d{19})/index\.html", html)
            ids.extend(found)
        except Exception as e:
            print(f"  列表页 {page_num} 获取失败: {e}")
            break

    # 去重并保持顺序
    seen = set()
    unique = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            unique.append(x)
    return unique


def parse_announcement(html: str) -> dict | None:
    """
    解析单条公告内容，提取逆回购操作数据。

    返回:
        {
            "date": "2026-06-18",
            "operations": [
                {"term": 7, "amount": 2480, "rate": 1.40},
                ...
            ]
        }
        如果没有逆回购操作则返回 None
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text()

    # 只处理逆回购相关的公告
    if "逆回购" not in text:
        return None

    # 提取操作日期
    date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not date_match:
        return None

    date_str = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"

    # 尝试从结构化表格提取数据
    # 方式1: 解析 HTML 表格
    tables = soup.find_all("table")
    operations = []

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            # 跳过表头行
            if any(h in "".join(cells) for h in ["期限", "操作", "招标", "中标"]):
                continue
            # 匹配数据行: [期限, 操作利率, 投标量, 中标量]
            # 或其他格式的行
            for i, cell in enumerate(cells):
                term_match = re.match(r"(\d+)天", cell)
                if term_match:
                    term = int(term_match.group(1))
                    # 利率和金额在后续 cells 中
                    rate = None
                    amount = None
                    for j in range(i + 1, min(i + 4, len(cells))):
                        rate_m = re.search(r"(\d+\.?\d*)%", cells[j])
                        amount_m = re.search(r"(\d+)亿", cells[j])
                        if rate_m and rate is None:
                            rate = float(rate_m.group(1))
                        if amount_m and amount is None:
                            amount = int(amount_m.group(1))
                    if term and amount:
                        operations.append({
                            "term": term,
                            "amount": amount,
                            "rate": rate or 0.0,
                        })
                    break

    # 方式2: 如果表格解析失败，从描述文本中提取
    if not operations:
        # 匹配: X亿元 Y天期 逆回购
        desc_match = re.search(
            r"(\d+)亿元\s*(\d+)天期\s*逆回购",
            text,
        )
        if desc_match:
            amount = int(desc_match.group(1))
            term = int(desc_match.group(2))
            rate_match = re.search(r"(\d+\.?\d*)%", text)
            rate = float(rate_match.group(1)) if rate_match else 0.0
            operations.append({"term": term, "amount": amount, "rate": rate})

    if not operations:
        return None

    return {"date": date_str, "operations": operations}


def parse_announcement_simple(html: str) -> dict | None:
    """
    简化版解析：使用正则表达式从文本中直接提取关键数据。
    作为 parse_announcement 的补充，处理表格解析失败的情况。
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text()

    if "逆回购" not in text:
        return None

    date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not date_match:
        return None

    date_str = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"

    operations = []

    # 正则匹配: "X亿元 Y天期 逆回购" 模式（支持多笔）
    # 例如: "开展了1000亿元7天期逆回购操作和2000亿元14天期逆回购操作"
    pattern = r"(\d+)亿元\s*(\d+)天期\s*逆回购"
    matches = re.findall(pattern, text)
    for m in matches:
        amount = int(m[0])
        term = int(m[1])
        # 尝试获取利率
        rate_match = re.search(r"(\d+\.?\d*)%", text)
        rate = float(rate_match.group(1)) if rate_match else 0.0
        operations.append({"term": term, "amount": amount, "rate": rate})

    if not operations:
        return None

    return {"date": date_str, "operations": operations}


def calculate_maturities(records: list) -> dict:
    """
    根据所有历史操作记录，计算每日到期量。

    对于每笔操作: 到期日 = 操作日 + 期限天数
    """
    from collections import defaultdict

    daily = defaultdict(lambda: {"operation": 0, "maturity": 0})

    for rec in records:
        op_date = rec["date"]
        daily[op_date]["operation"] += rec["total_amount"]

        # 计算到期日
        dt = datetime.strptime(op_date, "%Y-%m-%d")
        maturity_dt = dt + timedelta(days=rec["term"])
        maturity_str = maturity_dt.strftime("%Y-%m-%d")
        daily[maturity_str]["maturity"] += rec["total_amount"]

    # 计算净投放
    for d in daily.values():
        d["net"] = d["operation"] - d["maturity"]

    return dict(sorted(daily.items()))


def main():
    print("=" * 60)
    print("  央行逆回购数据抓取")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: 获取公告列表
    print("\n[1/3] 获取公告列表...")
    ids = get_announcement_ids(pages=20)  # 拉约300条，覆盖一年多
    print(f"  共获取 {len(ids)} 条公告 ID")

    # Step 2: 逐条解析
    print("\n[2/3] 解析公告内容...")
    records = []
    success_count = 0
    skip_count = 0

    for i, aid in enumerate(ids):
        url = f"{BASE_URL}/zhengcehuobisi/125207/125213/125431/125475/{aid}/index.html"
        try:
            html = fetch_page(url)
            # 先用简化版解析
            result = parse_announcement_simple(html)
            if result is None:
                skip_count += 1
                continue

            # 汇总该日操作
            total_amount = sum(op["amount"] for op in result["operations"])
            # 取最常见的期限（同一日通常期限相同）
            term = result["operations"][0]["term"] if result["operations"] else 7
            rate = result["operations"][0]["rate"] if result["operations"] else 0.0

            records.append({
                "date": result["date"],
                "term": term,
                "total_amount": total_amount,
                "rate": rate,
                "ops_detail": result["operations"],
            })
            success_count += 1

            if (i + 1) % 50 == 0:
                print(f"  进度: {i+1}/{len(ids)} (成功 {success_count}, 跳过 {skip_count})")

        except Exception as e:
            print(f"  解析失败 {aid}: {e}")
            skip_count += 1

        # 速率限制
        if (i + 1) % 10 == 0:
            import time
            time.sleep(0.3)

    print(f"  完成! 成功 {success_count} 条, 跳过 {skip_count} 条")

    # Step 3: 计算到期量并输出
    print("\n[3/3] 计算到期量并生成数据...")
    daily = calculate_maturities(records)

    # 构建输出格式
    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_records": len(records),
        "date_range": {
            "from": records[-1]["date"] if records else "",
            "to": records[0]["date"] if records else "",
        },
        "records": records,
        "daily": daily,
    }

    # 保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  数据已保存 → {OUTPUT_FILE}")
    print(f"  操作记录: {len(records)} 条")
    print(f"  覆盖日期: {len(daily)} 天")
    print("=" * 60)

    # Quick summary of latest operations
    if records:
        latest = records[0]
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_data = daily.get(today_str, {"operation": 0, "maturity": 0, "net": 0})
        print(f"\n  最新操作: {latest['date']} {latest['total_amount']}亿 {latest['term']}天期")
        print(f"  今日 ({today_str}):")
        print(f"    操作量: {today_data['operation']}亿")
        print(f"    到期量: {today_data['maturity']}亿")
        print(f"    净投放: {today_data['net']:+d}亿")


if __name__ == "__main__":
    main()
