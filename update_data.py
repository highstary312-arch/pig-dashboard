#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取五部门《生猪产品数据》：博亚和讯(主) + 中国畜牧业协会猪业分会(备)。
月份从文章标题提取；指标值采用"向前核对年月"的方式匹配，规避侧边栏干扰。
GH_TOKEN+GH_REPO 存在时经 GitHub API 写回（云函数模式），否则读写本地 index.html。"""
import re, json, time, html, base64, os, urllib.request, urllib.parse, pathlib, datetime, sys

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
HERE = pathlib.Path(__file__).parent
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "")
DATA_V = 3  # 数据结构版本，低于此值则重建

NUM = re.compile(r"\d+(?:\.\d+)?")
INT = re.compile(r"\d+")
TITLE_MONTH = re.compile(r"20\d\d年\d{1,2}月")
BEFORE_M = re.compile(r"20\d\d年\d{1,2}月份[^0-9]{0,10}$")
BEFORE_Q = re.compile(r"20\d\d年[1-4一二三四]季度末?[^0-9]{0,8}$")
CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4"}

CORES = [
    ("屠宰量", "monthly", ["生猪定点屠宰企业屠宰量"], False),
    ("二元母猪销售价格", "monthly", ["二元母猪销售价格"], False),
    ("仔猪价格", "monthly", ["全国仔猪价格", "仔猪价格"], False),
    ("生猪出场价格", "monthly", ["生猪出场价格", "生猪出厂价格"], False),
    ("大中城市批发市场白条猪价格", "monthly", ["36个大中城市批发市场白条猪价格"], False),
    ("全国批发市场白条猪价格", "monthly", ["全国批发市场白条猪价格"], False),
    ("县乡集贸市场猪肉零售价格", "monthly", ["县乡集贸市场猪肉零售价格"], False),
    ("能繁母猪存栏量", "quarterly", ["季度末能繁母猪存栏", "能繁母猪存栏"], True),
    ("生猪存栏量", "quarterly", ["季度末生猪存栏", "生猪存栏"], True),
]


def get_raw(url, timeout=40):
    req = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def fetch(url):
    try:
        return get_raw(url)
    except Exception:
        return get_raw("https://r.jina.ai/" + urllib.parse.quote(url, safe=":/?=&%"), timeout=90)


def strip_tags(h):
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h).replace("|", "，")
    return re.sub(r"\s+", "，", h)


def gh_api(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        **HDR, "Authorization": "Bearer " + GH_TOKEN, "Accept": "application/vnd.github+json"})
    return json.loads(urllib.request.urlopen(req, timeout=40).read().decode())


def load_html():
    if GH_TOKEN and GH_REPO:
        info = gh_api("GET", "https://api.github.com/repos/%s/contents/index.html?ref=main" % GH_REPO)
        return base64.b64decode(info["content"]).decode(), info["sha"]
    return (HERE / "index.html").read_text(encoding="utf-8"), None


def save_html(t, sha):
    if GH_TOKEN and GH_REPO:
        data = {"message": "chore: 更新生猪产品数据 %s" % datetime.date.today(),
                "content": base64.b64encode(t.encode()).decode(), "branch": "main"}
        if sha:
            data["sha"] = sha
        gh_api("PUT", "https://api.github.com/repos/%s/contents/index.html" % GH_REPO, data)
    else:
        (HERE / "index.html").write_text(t, encoding="utf-8")


def parse_text(text, db):
    mt = TITLE_MONTH.search(text[:90])   # 月份只从标题区域取
    if not mt:
        return 0
    s = mt.group(0)
    y, mo = s[:4], int(s[5:s.find("月")])
    mk = "%s-%02d" % (y, mo)
    added = 0

    def put(target, key, val, isint):
        nonlocal added
        grp = "quarterly" if "-Q" in key else "monthly"
        d = db[grp].setdefault(target, {"unit": "万头" if isint else "元/公斤", "data": {}})["data"]
        if key not in d:
            d[key] = int(val) if isint else float(val)
            added += 1

    def grab(core, target, isint):
        start = 0
        while True:
            i = text.find(core, start)
            if i < 0:
                return
            before = text[max(0, i - 22):i]
            ok = False
            key = mk
            mm2 = BEFORE_M.search(before)
            if mm2:
                sm = TITLE_MONTH.search(mm2.group(0))
                if sm and sm.group(0)[:4] == y and int(sm.group(0)[5:sm.group(0).find("月")]) == mo:
                    ok = True
            if not ok:
                q2 = BEFORE_Q.search(before)
                if q2:
                    sq = q2.group(0)
                    qq = CN_NUM.get(sq[5], sq[5])
                    key = "%s-Q%s" % (sq[:4], qq)
                    ok = True
            if ok:
                seg = text[i + len(core): i + len(core) + 25]
                n = (INT if isint else NUM).search(seg)
                if n:
                    put(target, key, n.group(0), isint)
                    return
            start = i + 1

    for target, grp, cores, isint in CORES:
        for core in cores:
            grab(core, target, isint)
    return added


def source_boyar(db):
    kw = "%E7%94%9F%E7%8C%AA%E4%BA%A7%E5%93%81%E6%95%B0%E6%8D%AE"
    ids = set()
    for page in range(1, 6):
        h = fetch("https://www.boyar.cn/search.html?keyword=%s&page=%d" % (kw, page))
        ids.update(re.findall(r"/article/(\d+)\.html", h))
        time.sleep(0.3)
    print("  博亚搜索: 文章链接 %d 个" % len(ids))
    ok = added = 0
    for aid in sorted(ids, key=int):
        try:
            t = strip_tags(fetch("https://www.boyar.cn/article/%s.html" % aid))
        except Exception:
            continue
        if "生猪产品数据" in t:
            try:
                a = parse_text(t, db)
                added += a
                ok += (1 if a > 0 else 0)
            except Exception:
                pass
        time.sleep(0.25)
    print("  博亚文章: 解析出月份 %d 篇 / 新增 %d 点" % (ok, added))


def source_caaa(db):
    links = set()
    for p in ["index"] + [str(i) for i in range(2, 14)]:
        h = fetch("https://pig.caaa.cn/html/pig_rd/pig_hydt/%s.html" % p)
        links.update(re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', h))
        time.sleep(0.3)
    print("  协会栏目: 文章链接 %d 个" % len(links))
    ok = added = 0
    for u in sorted(links):
        try:
            t = strip_tags(fetch(u))
        except Exception:
            continue
        if "生猪产品数据" in t:
            try:
                a = parse_text(t, db)
                added += a
                ok += (1 if a > 0 else 0)
            except Exception:
                pass
        time.sleep(0.25)
    print("  协会文章: 解析出月份 %d 篇 / 新增 %d 点" % (ok, added))


def main():
    html_text, sha = load_html()
    m = re.search(r'<script id="pig-data" type="application/json">(.*?)</script>', html_text, re.S)
    db = json.loads(m.group(1))
    if db.get("meta", {}).get("v", 0) < DATA_V:
        print("检测到旧版/受损数据，自动清空重建")
        db = {"meta": {"updated": "", "v": DATA_V}, "monthly": {}, "quarterly": {}}
    db.setdefault("monthly", {})
    db.setdefault("quarterly", {})

    crashed = 0
    for name, fn in [("博亚和讯", source_boyar), ("猪业分会", source_caaa)]:
        print("[%s]" % name)
        try:
            fn(db)
        except Exception as e:
            crashed += 1
            print("  源整体失败: %s" % e)
    if crashed == 2:
        print("错误：两个数据源全部失败")
        sys.exit(1)

    # 只保留 2023 年起的数据
    for grp in ("monthly", "quarterly"):
        for t, v in db[grp].items():
            v["data"] = {k: x for k, x in v["data"].items() if k >= "2023"}

    db["meta"]["updated"] = datetime.date.today().isoformat()
    db["meta"]["v"] = DATA_V
    new = '<script id="pig-data" type="application/json">\n%s\n</script>' % json.dumps(db, ensure_ascii=False, indent=1)
    save_html(re.sub(r'<script id="pig-data" type="application/json">.*?</script>', new, html_text, flags=re.S), sha)

    for t in [c[0] for c in CORES]:
        grp = "quarterly" if "存栏" in t else "monthly"
        ks = sorted(db[grp].get(t, {}).get("data", {}).keys())
        print("%s: %d 期 %s" % (t, len(ks), ks[:2] + ["..."] + ks[-2:] if len(ks) > 4 else ks))
    print("完成，更新日期 %s" % db["meta"]["updated"])


if __name__ == "__main__":
    main()
