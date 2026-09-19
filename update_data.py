#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多源抓取五部门《生猪产品数据》并更新 index.html。
说明：
- GitHub 机房 IP 会被国内站点 WAF 挑战，默认直连失败自动改走 r.jina.ai 阅读器代理。
- 环境变量 JINA: auto(默认) / always / off
- 若设置 GH_TOKEN + GH_REPO，则直接通过 GitHub API 写回仓库（供国内云函数使用）；
  否则读写本地 index.html（GitHub Actions 模式）。
"""
import re, json, time, html, base64, os, urllib.request, urllib.parse, pathlib, datetime, sys

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
HERE = pathlib.Path(__file__).parent
JINA = os.environ.get("JINA", "auto")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "")


def get_raw(url, timeout=40):
    req = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def fetch_jina(url):
    return get_raw("https://r.jina.ai/" + urllib.parse.quote(url, safe=":/?=&%"), timeout=90)


def fetch(url):
    """优先直连，失败则经 jina 代理；JINA=always 时全部走 jina。"""
    if JINA == "always":
        return fetch_jina(url)
    try:
        return get_raw(url)
    except Exception:
        if JINA == "off":
            raise
        return fetch_jina(url)


def strip_tags(h):
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h).replace("|", " ")
    return re.sub(r"\s+", "", h)


# ---------------- GitHub 读写（可选） ----------------

def gh_api(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        **HDR,
        "Authorization": "Bearer " + GH_TOKEN,
        "Accept": "application/vnd.github+json",
    })
    return json.loads(urllib.request.urlopen(req, timeout=40).read().decode())


def load_html():
    if GH_TOKEN and GH_REPO:
        info = gh_api("GET", "https://api.github.com/repos/%s/contents/index.html?ref=main" % GH_REPO)
        return base64.b64decode(info["content"]).decode(), info["sha"]
    return (HERE / "index.html").read_text(encoding="utf-8"), None


def save_html(t, sha):
    if GH_TOKEN and GH_REPO:
        data = {
            "message": "chore: 更新生猪产品数据 %s" % datetime.date.today(),
            "content": base64.b64encode(t.encode()).decode(),
            "branch": "main",
        }
        if sha:
            data["sha"] = sha
        gh_api("PUT", "https://api.github.com/repos/%s/contents/index.html" % GH_REPO, data)
    else:
        (HERE / "index.html").write_text(t, encoding="utf-8")


# ---------------- 数据解析 ----------------

def parse_text(text, db):
    """从一篇文章文本中提取 8 项指标。用字符串查找+取数，不用复杂分组正则。"""
    m = re.search(r"(20\d\d）年（\d{1,2}）月份", text)
    if not m:
        return 0
    mk = "%s-%02d" % (m.group(1), int(m.group(2)))
    prefix = "%s年%d月份" % (m.group(1), int(m.group(2)))
    added = 0

    def put(group, target, key, val, isint=False):
        nonlocal added
        d = db[group][target]["data"]
        if key not in d:
            d[key] = int(val) if isint else float(val)
            added += 1

    def grab(label, group, target):
        i = text.find(label)
        if i < 0:
            return
        seg = text[i + len(label): i + len(label) + 25]
        mm = re.search(r"(\d+(?:\.\d+)?)", seg)
        if mm:
            put(group, target, mk, mm.group(1))

    grab(prefix + "生猪定点屠宰企业屠宰量", "monthly", "屠宰量")
    grab(prefix + "全国二元母猪销售价格", "monthly", "二元母猪销售价格")
    grab(prefix + "全国仔猪价格", "monthly", "仔猪价格")
    grab(prefix + "生猪出场价格", "monthly", "生猪出场价格")
    grab(prefix + "生猪出厂价格", "monthly", "生猪出场价格")
    grab(prefix + "36个大中城市批发市场白条猪价格", "monthly", "大中城市批发市场白条猪价格")
    grab(prefix + "全国批发市场白条猪价格", "monthly", "全国批发市场白条猪价格")
    grab(prefix + "县乡集贸市场猪肉零售价格", "monthly", "县乡集贸市场猪肉零售价格")

    q = re.search(r"(20\d\d）年（[1-4]）季度末能繁母猪存栏", text)
    if q:
        seg = text[q.end(): q.end() + 25]
        mm = re.search(r"(\d+)", seg)
        if mm:
            put("quarterly", "能繁母猪存栏量", "%s-Q%s" % (q.group(1), q.group(2)), mm.group(1), True)
    q2 = re.search(r"(20\d\d）年（[1-4]）季度末生猪存栏", text)
    if q2:
        seg = text[q2.end(): q2.end() + 25]
        mm = re.search(r"(\d+)", seg)
        if mm:
            put("quarterly", "生猪存栏量", "%s-Q%s" % (q2.group(1), q2.group(2)), mm.group(1), True)
    return added


def safe_parse(t, db, stat):
    """单篇解析保护：一篇出错不影响整体。"""
    try:
        a = parse_text(t, db)
        stat[1] += a
        stat[0] += (1 if a > 0 else 0)
    except Exception as e:
        stat[2] += 1
        if stat[2] <= 2:
            print("    (某篇解析异常: %s)" % e)


# ---------------- 三个数据源 ----------------

def source_boyar(db):
    """主源：博亚和讯站内搜索，归档最全（2023 年至今每月一篇）。"""
    kw = "%E7%94%9F%E7%8C%AA%E4%BA%A7%E5%93%81%E6%95%B0%E6%8D%AE"
    ids = set()
    for page in range(1, 6):
        u = "https://www.boyar.cn/search.html?keyword=%s&page=%d" % (kw, page)
        h = fetch(u)
        found = re.findall(r"/article/(\d+)\.html", h)
        if page == 1 and not found and JINA != "always":
            print("  直连疑似被挑战，改走 jina 代理重试搜索页")
            found = re.findall(r"/article/(\d+)\.html", fetch_jina(u))
        ids.update(found)
        time.sleep(0.3)
    print("  博亚搜索: 收集到文章链接 %d 个" % len(ids))
    kw_hit = 0
    stat = [0, 0, 0]
    for aid in sorted(ids, key=int):
        try:
            t = strip_tags(fetch("https://www.boyar.cn/article/%s.html" % aid))
        except Exception:
            continue
        if "生猪产品数据" in t:
            kw_hit += 1
            safe_parse(t, db, stat)
        time.sleep(0.25)
    print("  博亚文章: 含关键词 %d 篇 / 解析出月份 %d 篇 / 新增 %d 点" % (kw_hit, stat[0], stat[1]))


def source_caaa(db):
    """备源1：中国畜牧业协会猪业分会行业动态栏目。"""
    links = set()
    for p in ["index"] + [str(i) for i in range(2, 14)]:
        h = fetch("https://pig.caaa.cn/html/pig_rd/pig_hydt/%s.html" % p)
        if p == "index":
            f0 = re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', h)
            if not f0 and JINA != "always":
                f0 = re.findall(
                    r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"',
                    fetch_jina("https://pig.caaa.cn/html/pig_rd/pig_hydt/index.html"))
            links.update(f0)
        else:
            links.update(re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', h))
        time.sleep(0.3)
    print("  协会栏目: 收集到文章链接 %d 个" % len(links))
    stat = [0, 0, 0]
    for u in links:
        try:
            t = strip_tags(fetch(u))
        except Exception:
            continue
        if "生猪产品数据" in t:
            safe_parse(t, db, stat)
        time.sleep(0.25)
    print("  协会文章: 解析出月份 %d 篇 / 新增 %d 点" % (stat[0], stat[1]))


def source_chinafeed(db):
    """备源2：中国饲料工业信息网数据资料栏目。"""
    links = {}
    for p in range(1, 13):
        u = "http://www.chinafeed.com.cn/shujuziliao/" if p == 1 else \
            "http://www.chinafeed.com.cn/shujuziliao/list-%d.html" % p
        try:
            h = fetch(u)
        except Exception:
            continue
        for m in re.finditer(r'href="(http://www\.chinafeed\.com\.cn/shujuziliao/show-\d+\.html)"[^>]*title="([^"]*)"', h):
            if "生猪产品数据" in m.group(2):
                links[m.group(1)] = 1
        time.sleep(0.3)
    print("  饲料栏目: 收集到生猪产品数据文章 %d 篇" % len(links))
    stat = [0, 0, 0]
    for u in links:
        try:
            safe_parse(strip_tags(fetch(u)), db, stat)
        except Exception:
            continue
        time.sleep(0.25)
    print("  饲料文章: 解析出月份 %d 篇 / 新增 %d 点" % (stat[0], stat[1]))


# ---------------- 主流程 ----------------

def main():
    html_text, sha = load_html()
    db = json.loads(re.search(
        r'<script id="pig-data" type="application/json">(.*?)</script>', html_text, re.S).group(1))

    crashed = 0
    for name, fn in [("博亚和讯", source_boyar), ("猪业分会", source_caaa), ("饲料信息网", source_chinafeed)]:
        print("[%s]" % name)
        try:
            fn(db)
        except Exception as e:
            crashed += 1
            print("  源整体失败: %s" % e)
    if crashed == 3:
        print("错误：三个数据源全部失败")
        sys.exit(1)

    db["meta"]["updated"] = datetime.date.today().isoformat()
    new = '<script id="pig-data" type="application/json">\n%s\n</script>' % json.dumps(db, ensure_ascii=False, indent=1)
    save_html(re.sub(r'<script id="pig-data" type="application/json">.*?</script>', new, html_text, flags=re.S), sha)
    print("完成，更新日期 %s" % db["meta"]["updated"])


if __name__ == "__main__":
    main()
