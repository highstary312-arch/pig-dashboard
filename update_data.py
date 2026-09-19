#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多源抓取五部门《生猪产品数据》：博亚和讯 -> 中国畜牧业协会猪业分会 -> 中国饲料工业信息网
按顺序尝试，只补空缺不覆盖已有数据；三个源全部失败才报错退出。"""
import re, json, time, html, urllib.request, pathlib, datetime, sys

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
HERE = pathlib.Path(__file__).parent

def get(url, retry=2):
    last = None
    for i in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=HDR)
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(2)
    raise last

def strip_tags(h):
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h)
    return re.sub(r"\s+", "", h)

def load_db():
    t = (HERE / "index.html").read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="pig-data" type="application/json">(.*?)</script>', t, re.S).group(1))

def save_db(db):
    p = HERE / "index.html"
    t = p.read_text(encoding="utf-8")
    new = f'<script id="pig-data" type="application/json">\n{json.dumps(db, ensure_ascii=False, indent=1)}\n</script>'
    p.write_text(re.sub(r'<script id="pig-data" type="application/json">.*?</script>', new, t, flags=re.S), encoding="utf-8")

def parse_text(text, db):
    """解析单篇文章（表格/文字格式通用），只补空缺，返回新增数据点数。"""
    m = re.search(r"(20\d\d）年（\d{1,2}）月份", text)
    if not m:
        return 0
    mk = f"{m.group(1)}-{int(m.group(2)):02d}"
    added = 0
    def put(group, target, key, val, isint=False):
        nonlocal added
        d = db[group][target]["data"]
        if key not in d:
            d[key] = int(val) if isint else float(val)
            added += 1
    def month_pat(label):
        return rf"20\d\d年\d{{1,2}}月份{label}（?：（?:元/公斤）|（万头））?\s*(\d+(?:\.\d+)?)"
    for label, target in [
        (r"(?：规模以上）?生猪定点屠宰企业屠宰量", "屠宰量"),
        (r"全国二元母猪销售价格", "二元母猪销售价格"),
        (r"全国仔猪价格", "仔猪价格"),
        (r"生猪出（?：场|厂）价格", "生猪出场价格"),
        (r"36个大中城市批发市场白条猪价格", "大中城市批发市场白条猪价格"),
        (r"全国批发市场白条猪价格", "全国批发市场白条猪价格"),
        (r"县乡集贸市场猪肉零售价格", "县乡集贸市场猪肉零售价格"),
    ]:
        mm = re.search(month_pat(label), text)
        if mm:
            put("monthly", target, mk, mm.group(1))
    mq = re.search(r"(20\d\d）年（[1-4]）季度末能繁母猪存栏（?：（万头））?（\d+)", text)
    if mq:
        put("quarterly", "能繁母猪存栏量", f"{mq.group(1)}-Q{mq.group(2)}", mq.group(3), True)
    hq = re.search(r"(20\d\d）年（[1-4]）季度末生猪存栏（?：（万头））?（\d+)", text)
    if hq:
        put("quarterly", "生猪存栏量", f"{hq.group(1)}-Q{hq.group(2)}", hq.group(3), True)
    return added

def source_boyar(db):
    """主源：博亚和讯站内搜索《生猪产品数据》，归档完整（2023年至今）。"""
    kw = "%E7%94%9F%E7%8C%AA%E4%BA%A7%E5%93%81%E6%95%B0%E6%8D%AE"
    ids = set()
    for page in range(1, 6):
        h = get(f"https://www.boyar.cn/search.html?keyword={kw}&page={page}")
        ids.update(re.findall(r"/article/(\d+)\.html", h))
        time.sleep(0.3)
    total = 0
    for aid in sorted(ids, key=int):
        try:
            t = strip_tags(get(f"https://www.boyar.cn/article/{aid}.html"))
            if "生猪产品数据" in t:
                total += parse_text(t, db)
        except Exception:
            pass
        time.sleep(0.25)
    return total

def source_caaa(db):
    """备源1：中国畜牧业协会猪业分会行业动态栏目。"""
    links = set()
    for p in ["index"] + [str(i) for i in range(2, 14)]:
        h = get(f"https://pig.caaa.cn/html/pig_rd/pig_hydt/{p}.html")
        links.update(re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', h))
        time.sleep(0.3)
    total = 0
    for u in links:
        try:
            t = strip_tags(get(u))
            if "生猪产品数据" in t:
                total += parse_text(t, db)
        except Exception:
            pass
        time.sleep(0.25)
    return total

def source_chinafeed(db):
    """备源2：中国饲料工业信息网数据资料栏目。"""
    links = {}
    for p in range(1, 36):
        u = f"http://www.chinafeed.com.cn/shujuziliao/" if p == 1 else f"http://www.chinafeed.com.cn/shujuziliao/list-{p}.html"
        try:
            h = get(u)
        except Exception:
            continue
        for m in re.finditer(r'href="(http://www\.chinafeed\.com\.cn/shujuziliao/show-\d+\.html)"[^>]*title="([^"]*)"', h):
            if "生猪产品数据" in m.group(2):
                links[m.group(1)] = 1
        time.sleep(0.3)
    total = 0
    for u in links:
        try:
            total += parse_text(strip_tags(get(u)), db)
        except Exception:
            pass
        time.sleep(0.25)
    return total

def main():
    db = load_db()
    failed = []
    total_new = 0
    for name, fn in [("博亚和讯", source_boyar), ("猪业分会", source_caaa), ("饲料信息网", source_chinafeed)]:
        try:
            n = fn(db)
            print(f"[{name}] 新增数据点: {n}")
            total_new += n
        except Exception as e:
            print(f"[{name}] 抓取失败: {e}")
            failed.append(name)
    if total_new == 0 and len(failed) == 3:
        print("错误：三个数据源全部抓取失败，未更新任何数据")
        sys.exit(1)
    db["meta"]["updated"] = datetime.date.today().isoformat()
    save_db(db)
    print(f"完成，本次共新增 {total_new} 个数据点，更新日期 {db['meta']['updated']}")

if __name__ == "__main__":
    main()
