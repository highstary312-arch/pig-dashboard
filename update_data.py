#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取五部门《生猪产品数据》：博亚和讯(主) + 中国畜牧业协会猪业分会(备)。
要点：
- 月份从标题取；支持"2026年7月"常规标题，也支持"2025年上半年及6月份"/"全年及12月份"/
  "前三季度及9月份"等特殊标题；
- 指标值采用"向前核对年月/季度"匹配，兼容 月份/月末/季度末 及 全国/规模以上 前缀；
- 存栏类指标并入月度时间轴（月末->当月；季度末->3/6/9/12月），前期月度、后期季度一图展示；
- 部分月份官方/转载站以图片形式发布（正文无文字），无法程序解析，用 MANUAL_PATCH 人工补录，
  补录值已与官方累计数交叉核验（2026上半年屠宰量合计=22725万头、2025年1-11月=36246万头）；
- 遇到"有标题但正文是图片"的新文章会在日志里大声告警，提示需要人工补录，不再静默缺月；
- GH_TOKEN+GH_REPO 存在时经 GitHub API 写回（云函数模式），否则读写本地 index.html。"""
import re, json, time, html, base64, os, urllib.request, urllib.parse, pathlib, datetime, sys, traceback

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
HERE = pathlib.Path(__file__).parent
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "")
DATA_V = 4

NUM = re.compile(r"\d+(?:\.\d+)?")
INT = re.compile(r"\d+")
TITLE_MONTH = re.compile(r"20\d\d年\d{1,2}月")
TITLE_SPECIAL = re.compile(r"(20\d\d)年(?:上半年|一季度|前三季度|全年|下半年)及(\d{1,2})月")
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

# 图片版文章人工补录（农业农村部发布原表读数，已用官方累计数交叉核验）
# 注：能繁母猪存栏/生猪存栏为季度末值（2季度末=6月末）
MANUAL_PATCH = {
    "屠宰量": {"2025-06": 3006, "2026-05": 3912, "2026-06": 3820},
    "二元母猪销售价格": {"2025-06": 33.60, "2026-05": 27.61, "2026-06": 26.77},
    "仔猪价格": {"2025-06": 37.25, "2026-05": 23.35, "2026-06": 22.54},
    "生猪出场价格": {"2025-06": 14.57, "2026-05": 9.80, "2026-06": 9.71},
    "大中城市批发市场白条猪价格": {"2025-06": 20.38, "2026-05": 16.18, "2026-06": 16.00},
    "全国批发市场白条猪价格": {"2025-06": 20.30, "2026-05": 14.88, "2026-06": 14.48},
    "县乡集贸市场猪肉零售价格": {"2025-06": 25.29, "2026-05": 20.03, "2026-06": 19.74},
    "能繁母猪存栏量": {"2025-06": 4043, "2026-06": 3780},
    "生猪存栏量": {"2025-06": 42447, "2026-06": 42491},
}


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


def title_month(text):
    """从标题取 (年, 月)。支持常规'2026年7月'与'上半年及6月份'等特殊标题。"""
    head = text[:200]
    m = TITLE_MONTH.search(head)
    if m:
        s = m.group(0)
        return s[:4], int(s[5:s.find("月")])
    m = TITLE_SPECIAL.search(head)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def parse_text(text, db):
    y, mo = title_month(text)
    if not y:
        return 0
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


def looks_like_image_article(raw):
    """正文是图片（ueditor上传图）而非文字表格，程序无法解析。"""
    return "/ueditor/" in raw or re.search(r'<img[^>]+upload', raw) is not None


def crawl(db, name, article_urls):
    ok = added = 0
    for u in article_urls:
        try:
            raw = fetch(u)
            t = strip_tags(raw)
        except Exception:
            continue
        if "生猪产品数据" in t:
            try:
                a = parse_text(t, db)
                added += a
                ok += (1 if a > 0 else 0)
                if a == 0 and looks_like_image_article(raw):
                    y, mo = title_month(t)
                    miss = y and ("%s-%02d" % (y, mo)) not in db["monthly"].get("屠宰量", {}).get("data", {})
                    if miss:
                        print("  !!! 警告：%s年%s月文章正文为图片，无法自动解析，请人工补录到 MANUAL_PATCH: %s"
                              % (y, mo, u))
            except Exception:
                traceback.print_exc()
        time.sleep(0.25)
    print("  %s: 解析出月份 %d 篇 / 新增 %d 点" % (name, ok, added))


def source_boyar(db):
    kw = "%E7%94%9F%E7%8C%AA%E4%BA%A7%E5%93%81%E6%95%B0%E6%8D%AE"
    ids = set()
    for page in range(1, 6):
        h = fetch("https://www.boyar.cn/search.html?keyword=%s&page=%d" % (kw, page))
        ids.update(re.findall(r"/article/(\d+)\.html", h))
        time.sleep(0.3)
    print("  博亚搜索: 文章链接 %d 个" % len(ids))
    crawl(db, "博亚文章", ["https://www.boyar.cn/article/%s.html" % aid for aid in sorted(ids, key=int)])


def source_caaa(db):
    links = set()
    for p in ["index"] + [str(i) for i in range(2, 14)]:
        h = fetch("https://pig.caaa.cn/html/pig_rd/pig_hydt/%s.html" % p)
        links.update(re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', h))
        time.sleep(0.3)
    print("  协会栏目: 文章链接 %d 个" % len(links))
    crawl(db, "协会文章", sorted(links))


def apply_manual_patch(db):
    n = 0
    for t, patch in MANUAL_PATCH.items():
        d = db["monthly"].setdefault(t, {"unit": UNITS.get(t, "元/公斤"), "data": {}})["data"]
        for k, v in patch.items():
            if k not in d:
                d[k] = v
                n += 1
    if n:
        print("人工补录: 新增 %d 点" % n)


def sanity_check(db):
    """月度指标连续性检查：缺月在日志中列出，便于发现问题。"""
    bad = 0
    for t, cores, isint, inv in TARGETS:
        data = db["monthly"].get(t, {}).get("data", {})
        ks = sorted(k for k in data if "-Q" not in k)
        if not ks:
            continue
        y, m = int(ks[0][:4]), int(ks[0][5:])
        ey, em = int(ks[-1][:4]), int(ks[-1][5:])
        missing = []
        while (y, m) <= (ey, em):
            k = "%d-%02d" % (y, m)
            if k not in data:
                missing.append(k)
            m += 1
            if m == 13:
                y, m = y + 1, 1
        # 存栏类指标后期改为季度发布，非季度月缺值属正常
        if inv:
            missing = [k for k in missing if int(k[5:]) in (3, 6, 9, 12)]
        if missing:
            bad += 1
            print("  !!! 缺失月份提醒 [%s]: %s" % (t, " ".join(missing)))
    if not bad:
        print("  连续性检查: 全部月度指标无缺月")


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
        except Exception:
            crashed += 1
            print("  源整体失败:")
            traceback.print_exc()
    if crashed == 2:
        print("错误：两个数据源全部失败")
        sys.exit(1)

    apply_manual_patch(db)

    for t, v in db["monthly"].items():
        v["data"] = {k: x for k, x in v["data"].items() if k >= "2023-01"}
    db["meta"]["updated"] = datetime.date.today().isoformat()
    db["meta"]["v"] = DATA_V
    new = '<script id="pig-data" type="application/json">\n%s\n</script>' % json.dumps(db, ensure_ascii=False, indent=1)
    save_html(re.sub(r'<script id="pig-data" type="application/json">.*?</script>', new, html_text, flags=re.S), sha)

    for t in [x[0] for x in TARGETS]:
        ks = sorted(db["monthly"].get(t, {}).get("data", {}).keys())
        print("%s: %d 期 %s .. %s" % (t, len(ks), ks[0] if ks else "-", ks[-1] if ks else "-"))
    sanity_check(db)
    print("完成，更新日期 %s" % db["meta"]["updated"])


if __name__ == "__main__":
    main()
