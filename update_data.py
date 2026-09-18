#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每周日运行：抓取最新《生猪产品数据》并更新 index.html 内嵌数据
仅使用 Python 标准库，无需 pip install 任何依赖。
首次运行会回填 2023 年以来的全部历史数据（约需几分钟）。"""
import re, json, time, urllib.request, pathlib, datetime

BASE = "http://www.chinafeed.com.cn"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
HERE = pathlib.Path(__file__).parent

def get(url):
    req = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")

def load_db():
    html = (HERE / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<script id="pig-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))

def save_db(db):
    p = HERE / "index.html"
    html = p.read_text(encoding="utf-8")
    new = f'<script id="pig-data" type="application/json">\n{json.dumps(db, ensure_ascii=False, indent=1)}\n</script>'
    html = re.sub(r'<script id="pig-data" type="application/json">.*?</script>', new, html, flags=re.S)
    p.write_text(html, encoding="utf-8")

def find_articles():
    """爬取数据资料栏目，收集所有《生猪产品数据》文章链接"""
    links = {}
    for p in range(1, 36):
        url = f"{BASE}/shujuziliao/" if p == 1 else f"{BASE}/shujuziliao/list-{p}.html"
        try:
            h = get(url)
        except Exception as e:
            print("列表页获取失败", url, e)
            continue
        for m in re.finditer(r'href="(http://www\.chinafeed\.com\.cn/shujuziliao/show-\d+\.html)"[^>]*title="([^"]*)"', h):
            if "生猪产品数据" in m.group(2):
                links[m.group(1)] = m.group(2)
        time.sleep(0.3)
    return links

def parse_article(html, db):
    m = re.search(r'(20\d\d）年（\d{1,2}）月份', html)
    if not m:
        return
    mk = f"{m.group(1)}-{int(m.group(2)):02d}"
    y = mk[:4]

    def grab(pat, target):
        mm = re.search(pat, html)
        if mm:
            db["monthly"][target]["data"][mk] = float(mm.group(1))

    grab(r'（?：生猪定点屠宰企业|规模以上生猪定点屠宰企业）屠宰量\s*(\d+(?:\.\d+)?)\s*万头', "屠宰量")
    grab(r'二元母猪销售价格\s*(\d+(?:\.\d+)?)\s*元/公斤', "二元母猪销售价格")
    grab(r'全国仔猪价格\s*(\d+(?:\.\d+)?)\s*元/公斤', "仔猪价格")
    grab(r'生猪出（?：场|厂）价格\s*(\d+(?:\.\d+)?)\s*元/公斤', "生猪出场价格")
    grab(r'36个大中城市批发市场白条猪价格\s*(\d+(?:\.\d+)?)\s*元/公斤', "大中城市批发市场白条猪价格")
    grab(r'县乡集贸市场猪肉零售价格\s*(\d+(?:\.\d+)?)\s*元/公斤', "县乡集贸市场猪肉零售价格")

    # 季度数据：仅取季度末月（3/6/9/12月）发布的官方值
    if mk[5:] in ("03", "06", "09", "12"):
        q = str((int(mk[5:]) - 1) // 3 + 1)
        mm = re.search(r'能繁母猪存栏\s*(\d+)\s*万头', html)
        if mm:
            db["quarterly"]["能繁母猪存栏量"]["data"][f"{y}-Q{q}"] = int(mm.group(1))
        mm = re.search(r'生猪存栏量?\s*(\d+)\s*万头', html)
        if mm and "季度" in html[max(0, mm.start()-60):mm.start()]:
            db["quarterly"]["生猪存栏量"]["data"][f"{y}-Q{q}"] = int(mm.group(1))

def main():
    db = load_db()
    articles = find_articles()
    print(f"发现 {len(articles)} 篇生猪产品数据文章")
    for i, url in enumerate(sorted(articles), 1):
        try:
            parse_article(get(url), db)
            print(f"[{i}/{len(articles)}] OK {url}")
        except Exception as e:
            print("解析失败", url, e)
        time.sleep(0.3)
    db["meta"]["updated"] = datetime.date.today().isoformat()
    save_db(db)
    print("更新完成：", db["meta"]["updated"])

if __name__ == "__main__":
    main()