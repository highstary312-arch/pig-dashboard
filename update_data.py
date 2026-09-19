#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多源抓取五部门《生猪产品数据》。
GitHub 机房 IP 会被国内站点 WAF 挑战，默认直连失败自动走 r.jina.ai 阅读器代理。
环境变量 JINA: auto(默认) / always / off；GH_TOKEN+GH_REPO 存在时直接写回 GitHub（供国内云函数使用）。"""
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

def gh_api(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        **HDR, "Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"})
    return json.loads(urllib.request.urlopen(req, timeout=40).read().decode())

def load_html():
    if GH_TOKEN and GH_REPO:
        info = gh_api("GET", f"https://api.github.com/repos/{GH_REPO}/contents/index.html?ref=main")
        return base64.b64decode(info["content"]).decode(), info["sha"]
    return (HERE / "index.html").read_text(encoding="utf-8"), None

def save_html(t, sha):
    if GH_TOKEN and GH_REPO:
        data = {"message": f"chore: 更新生猪产品数据 {datetime.date.today()}",
                "content": base64.b64encode(t.encode()).decode(), "branch": "main"}
        if sha:
            data["sha"] = sha
        gh_api("PUT", f"https://api.github.com/repos/{GH_REPO}/contents/index.html", data)
    else:
        (HERE / "index.html").write_text(t, encoding="utf-8")

def parse_text(text, db):
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
    def mp(label):
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
        mm = re.search(mp(label), text)
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
    kw = "%E7%94%9F%E7%8C%AA%E4%BA%A7%E5%93%81%E6%95%B0%E6%8D%AE"
    ids = set()
    for page in range(1, 6):
        u = f"https://www.boyar.cn/search.html?keyword={kw}&page={page}"
        h = fetch(u)
        found = re.findall(r"/article/(\d+)\.html", h)
        if page == 1 and not found and JINA != "always":
            print("  直连疑似被挑战，改走 jina 代理重试搜索页")
            found = re.findall(r"/article/(\d+)\.html", fetch_jina(u))
        ids.update(found)
        time.sleep(0.3)
    print(f"  博亚搜索: 收集到文章链接 {len(ids)} 个")
    kw_hit = ok = added = 0
    for aid in sorted(ids, key=int):
        try:
            t = strip_tags(fetch(f"https://www.boyar.cn/article/{aid}.html"))
        except Exception:
            continue
        if "生猪产品数据" in t:
            kw_hit += 1
            a = parse_text(t, db)
            added += a
            ok += (a > 0)
        time.sleep(0.25)
    print(f"  博亚文章: 含关键词 {kw_hit} 篇 / 解析出月份 {ok} 篇 / 新增 {added} 点")

def source_caaa(db):
    links = set()
    for p in ["index"] + [str(i) for i in range(2, 14)]:
        h = fetch(f"https://pig.caaa.cn/html/pig_rd/pig_hydt/{p}.html")
        if p == "index":
            f0 = re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', h)
            if not f0 and JINA != "always":
                f0 = re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', fetch_jina(f"https://pig.caaa.cn/html/pig_rd/pig_hydt/{p}.html"))
            links.update(f0)
        else:
            links.update(re.findall(r'href="(https://pig\.caaa\.cn/html/pig_rd/pig_hydt/[0-9/]+\.html)"', h))
        time.sleep(0.3)
    print(f"  协会栏目: 收集到文章链接 {len(links)} 个")
    ok = added = 0
    for u in links:
        try:
            t = strip_tags(fetch(u))
        except Exception:
            continue
        if "生猪产品数据" in t:
            a = parse_text(t, db)
            added += a
            ok += (a > 0)
        time.sleep(0.25)
    print(f"  协会文章: 解析出月份 {ok} 篇 / 新增 {added} 点")

def source_chinafeed(db):
    links = {}
    for p in range(1, 13):
        u = "http://www.chinafeed.com.cn/shujuziliao/" if p == 1 else f"http://www.chinafeed.com.cn/shujuziliao/list-{p}.html"
        try:
            h = fetch(u)
        except Exception:
            continue
        for m in re.finditer(r'href="(http://www\.chinafeed\.com\.cn/shujuziliao/show-\d+\.html)"[^>]*title="([^"]*)"', h):
            if "生猪产品数据" in m.group(2):
                links[m.group(1)] = 1
        time.sleep(0.3)
    print(f"  饲料栏目: 收集到生猪产品数据文章 {len(links)} 篇")
    ok = added = 0
    for u in links:
        try:
            a = parse_text(strip_tags(fetch(u)), db)
            added += a
            ok += (a > 0)
        except Exception:
            continue
        time.sleep(0.25)
    print(f"  饲料文章: 解析出月份 {ok} 篇 / 新增 {added} 点")

def main():
    html_text, sha = load_html()
    db = json.loads(re.search(r'<script id="pig-data" type="application/json">(.*?)</script>', html_text, re.S).group(1))
    crashed = 0
    for name, fn in [("博亚和讯", source_boyar), ("猪业分会", source_caaa), ("饲料信息网", source_chinafeed)]:
        print(f"[{name}]")
        try:
            fn(db)
        except Exception as e:
            crashed += 1
            print(f"  源整体失败: {e}")
    if crashed == 3:
        print("错误：三个数据源全部失败")
        sys.exit(1)
    db["meta"]["updated"] = datetime.date.today().isoformat()
    new = f'<script id="pig-data" type="application/json">\n{json.dumps(db, ensure_ascii=False, indent=1)}\n</script>'
    save_html(re.sub(r'<script id="pig-data" type="application/json">.*?</script>', new, html_text, flags=re.S), sha)
    print(f"完成，更新日期 {db['meta']['updated']}")

if __name__ == "__main__":
    main()
