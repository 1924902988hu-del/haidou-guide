"""公共工具:限速抓取 + op.gg RSC flight 解码。仅用标准库。"""
import json
import http.client
import re
import time
import urllib.request
import urllib.error
import urllib.parse

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_last_fetch = {}


def fetch(url, *, min_interval=0.0, retries=3, timeout=30, binary=False):
    """按 host 限速的 GET,失败退避重试;返回 str(或 binary=True 时 bytes)。"""
    host = urllib.parse.urlparse(url).netloc
    wait = _last_fetch.get(host, 0) + min_interval - time.time()
    if wait > 0:
        time.sleep(wait)
    last_err = None
    for attempt in range(retries):
        _last_fetch[host] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                try:
                    data = resp.read()
                except http.client.IncompleteRead as e:
                    # op.gg 偶尔在主体已完整时误报 Content-Length。仅接收足够大的
                    # 部分响应,调用方仍会对解析后的字段做业务完整性验证。
                    if len(e.partial) < 100_000:
                        raise
                    data = e.partial
                return data if binary else data.decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, http.client.IncompleteRead,
                TimeoutError, ConnectionError) as e:
            last_err = e
            # 404 不重试,直接抛给调用方做 slug 回退
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"fetch failed after {retries} tries: {url}: {last_err}")


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


def decode_flight(html):
    """从 Next.js App Router 页面中拼出 RSC flight 文本。"""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)', html)
    out = []
    for c in chunks:
        # flight 载荷是 JS 字符串字面量;unicode_escape 处理 \n \" \uXXXX
        out.append(c.encode("utf-8", errors="replace").decode("unicode_escape"))
    return "".join(out)


def extract_json_object(text, anchor_pos):
    """从 anchor_pos 往左找到最近的 '{',按括号配平截取完整 JSON 对象并解析。"""
    start = text.rfind("{", 0, anchor_pos)
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, min(len(text), start + 200_000)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.rfind("{", 0, start)
    return None


def save_json(path, obj):
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
