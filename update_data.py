#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取五部门《生猪产品数据》：博亚和讯(主) + 中国畜牧业协会猪业分会(备)。
要点：
- 月份从标题取；指标值采用"向前核对年月/季度"匹配，兼容 月份/月末/季度末 及 全国/规模以上 前缀；
- 存栏类指标并入月度时间轴（月末->当月；季度末->3/6/9/12月），前期月度、后期季度一图展示；
- GH_TOKEN+GH_REPO 存在时经 GitHub API 写回（云函数模式），否则读写本地 index.html。"""
import re, json, time, html, base64, os, urllib.request, urllib.parse, pathlib, datetime, sys

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; Win64; x64) AppleWebKit/537.36".replace("Win64; Win64", "Win64")}
HERE = pathlib.Path(__file__).parent
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "")
DATA_V = 4

NUM = re.compile(r"\d+(?:\.\d+)?")
INT = re.compile(r"\d+")
TITLE_MONTH = re.compile(r"20\d\d年\d{1,2}月")
BEFORE_M = re.compile(r"20\d\d年\d{1,2}月[份末][^0-9]{0,10}$")
BEFORE_Q = re.compile(r"20\d\d年[1-4一二三四]季度末?[^0-9]{0,8}$")
CN = {"一": "1", "二": "2", "三": "3", "四": "4"}

# (目标名, 匹配词列表, 是否整数, 是否存栏类)
TARGETS = [
    ("屠宰量", ["生猪定点屠宰企业屠宰量"], False, False),
    ("二元母猪销售价格", ["二元母猪销售价格"], False, False),
    ("仔猪价格", ["全国仔猪价格", "仔猪价格"], False, False),
    ("生猪出场价格", ["生猪出场价格", "生猪出厂价格"], False, False),
    ("大中城市批发市场白条猪价格", ["36个大中城市批发市场白条猪价格"], False, False),
    ("全国批发市场白条猪价格", ["全国批发市场白条猪价格"], False, False),
    ("县乡集贸市场猪肉零售价格", ["县乡集贸市场猪肉零售价格"], False, False),
    ("能繁母猪存栏量", ["能繁母猪存栏"], True, True),
    ("生猪存栏量", ["季度末生猪存栏", "生猪存栏"], True, True),
]
UNITS = {"屠宰量": "万头", "能繁母猪存栏量": "万头", "生猪存栏量": "万头"}
SEED = {"屠宰量": {"2023-01": 2896}}  # 官方1-2月累计5156减2月2260推算


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
    h = re.sub(r"(?i)</(td|tr|p|li|h1|h2|h3|h4|div|table)>", "，", h)
    h = re.sub(r"(?i)<br\s*/?>", "，", h)
    h = re.sub(r"<[^>]+>", "", h)          # 行内span直接去掉，避免"2023 年"被逗号隔开
    h = html.unescape(h).replace("|", "，")
    return re.sub(r"\s+", "", h)


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
    mt = TITLE_MONTH.search(text[:90])
    if not mt:
        return 0
    s = mt.group(0)
    y, mo = s[:4], int(s[5:s.find("月")])
    mk = "%s-%02d" % (y, mo)
    added = 0

    def put(target, key, val, isint):
        nonlocal added
        d = db["monthly"].setdefault(target, {"unit": UNITS.get(target, "元/公斤"), "data": {}})["data"]
        if key not in d:
            d[key] = int(val) if isint else float(val)
            added += 1

    for name, cores, isint, inv in TARGETS:
        for core in cores:
            start = 0
            hit = False
            while not hit:
                i = text.find(core, start)
                if i < 0:
                    break
                before = text[max(0, i - 22):i]
                key = None
                mm2 = BEFORE_M.search(before)
                if mm2:
                    sm = TITLE_MONTH.search(mm2.group(0))
                    if sm and sm.group(0)[:4] == y and int(sm.group(0)[5:sm.group(0).find("月")]) == mo:
                        key = mk
                if key is None and inv:
                    q2 = BEFORE_Q.search(before)
                    if q2:
                        sq = q2.group(0)
                        key = "%s-%02d" % (sq[:4], int(CN.get(sq[5], sq[5])) * 3)
                if key:
                    seg = text[i + len(core): i + len(core) + 25]
                    n = (INT if isint else NUM).search(seg)
                    if n:
                        put(name, key, n.group(0), isint)
                        hit = True
                start = i + 1
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
        db = {"meta": {"updated": "", "v": DATA_V}, "monthly": {}}
    db.pop("quarterly", None)
    db.setdefault("monthly", {})
    for t, seed in SEED.items():
        d = db["monthly"].setdefault(t, {"unit": UNITS.get(t, "元/公斤"), "data": {}})["data"]
        for k, v in seed.items():
            d.setdefault(k, v)

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

    for t, v in db["monthly"].items():
        v["data"] = {k: x for k, x in v["data"].items() if k >= "2023-01"}
    db["meta"]["updated"] = datetime.date.today().isoformat()
    db["meta"]["v"] = DATA_V
    new = '<script id="pig-data" type="application/json">\n%s\n</script>' % json.dumps(db, ensure_ascii=False, indent=1)
    save_html(re.sub(r'<script id="pig-data" type="application/json">.*?</script>', new, html_text, flags=re.S), sha)

    for t in [x[0] for x in TARGETS]:
        ks = sorted(db["monthly"].get(t, {}).get("data", {}).keys())
        print("%s: %d 期 %s .. %s" % (t, len(ks), ks[0] if ks else "-", ks[-1] if ks else "-"))
    print("完成，更新日期 %s" % db["meta"]["updated"])


if __name__ == "__main__":
    main()
