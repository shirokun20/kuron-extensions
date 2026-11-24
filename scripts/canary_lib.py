#!/usr/bin/env python3
"""Shared helpers for the canary scripts (stdlib only).

Used by ``canary_probe.py`` and ``canary_smoke_runner.py``:

* config discovery + route URL building for ``GenericScraper`` (HTML) and
  ``GenericRest`` (JSON) configs,
* a polite HTTP client (timeout, jitter, retry-on-429/503, identity encoding),
* a tiny HTML DOM + CSS selector engine covering the subset of selectors the
  120 configs actually use (tag / #id / .class / [attr op value] / descendant
  / ``>`` / ``+`` / ``,`` / ``:not`` / ``:first-child`` / ``:last-child`` /
  ``:nth-child`` / ``:contains``), so "1 valid item" checks work without
  lxml/bs4,
* a JSONPath-ish extractor (``$.items[*]``) for REST configs.

The selector engine is deliberately forgiving: pseudo-classes it does not know
are ignored (they only widen a match), because canary checks care about
"did upstream HTML change shape", not pixel-exact extraction.

Refs #62, #64.
"""

import glob
import gzip
import html as html_mod
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_GLOB = os.path.join(ROOT, "config", "*", "*-config.json")
BUCKETS = ("id", "en", "ja", "vi", "ch", "global")
DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/122.0.0.0 Safari/537.36")
SEARCH_QUERIES = (
    "love", "manga", "comic", "girl", "magic", "school", "dragon", "star",
)


# ---------------------------------------------------------------------------
# config discovery

def resolve_list_block(cfg, name, depth=0):
    """Resolve the list block for a named HTML route, following `inherits`."""
    entry = url_patterns(cfg).get(name)
    selectors = (cfg.get("scraper") or {}).get("selectors") or {}
    if not isinstance(entry, dict):
        if entry is None and name not in ("home", "search"):
            return selectors.get("list")
        return selectors.get("list") if entry is None else None
    if isinstance(entry.get("list"), dict):
        return entry["list"]
    parent = entry.get("inherits")
    if parent and depth < 4:
        return resolve_list_block(cfg, parent, depth + 1)
    if selectors.get("list"):
        return selectors["list"]
    if name != "home" and depth < 4:
        return resolve_list_block(cfg, "home", depth + 1)
    return None

# ---------------------------------------------------------------------------

def load_configs(bucket=None, source=None):
    """Yield (bucket, relpath, config) for config/*/*-config.json."""
    rows = []
    for path in sorted(glob.glob(CONFIG_GLOB)):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        cfg_bucket = rel.split("/")[1]
        if bucket and cfg_bucket != bucket:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            continue
        if source and cfg.get("source") != source:
            continue
        rows.append((cfg_bucket, rel, cfg))
    return rows


def search_queries():
    return SEARCH_QUERIES


def substitute(template, values):
    out = template or ""
    for key, value in values.items():
        out = out.replace("{%s}" % key, str(value))
    return out


def absolute_url(cfg, url):
    if not url:
        return ""
    url = url.replace("{origin}", (cfg.get("baseUrl") or "").rstrip("/"))
    if url.startswith(("http://", "https://")):
        return url
    base = (cfg.get("baseUrl") or "").rstrip("/")
    if not url.startswith("/"):
        url = "/" + url
    return base + url


def url_patterns(cfg):
    return ((cfg.get("scraper") or {}).get("urlPatterns") or {})


def pattern_url(cfg, name):
    entry = url_patterns(cfg).get(name)
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("url") or ""
    return ""


def api_endpoint(cfg, *names):
    api = cfg.get("api") or {}
    endpoints = api.get("endpoints") or {}
    for name in names:
        entry = endpoints.get(name)
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict) and entry.get("path"):
            return entry["path"]
    return ""


def home_target(cfg):
    """Return (kind, url) for the home route, or (None, reason)."""
    url = pattern_url(cfg, "home")
    if url:
        return "html", absolute_url(cfg, substitute(url, {"page": 1}))
    url = api_endpoint(cfg, "latest", "allGalleries", "browse")
    if url:
        base = (cfg.get("api") or {}).get("url") or cfg.get("baseUrl")
        url = substitute(url, {"page": 1, "offset": 0, "query": ""})
        return "api", base.rstrip("/") + "/" + url.lstrip("/")
    return None, "no home/latest route in config"


def search_target(cfg, query):
    url = pattern_url(cfg, "search")
    if url:
        return "html", absolute_url(cfg, substitute(url, {"query": query, "page": 1}))
    url = api_endpoint(cfg, "search")
    if url:
        base = (cfg.get("api") or {}).get("url") or cfg.get("baseUrl")
        url = substitute(url, {"query": query, "q": query, "page": 1,
                               "offset": 0})
        return "api", base.rstrip("/") + "/" + url.lstrip("/")
    return None, "no search route in config"


def detail_target(cfg, item_id):
    if not item_id:
        return None, "no sample item id from home"
    url = pattern_url(cfg, "detail")
    if url:
        return "html", absolute_url(cfg, substitute(url, {"id": item_id}))
    url = api_endpoint(cfg, "detail")
    if url:
        base = (cfg.get("api") or {}).get("url") or cfg.get("baseUrl")
        return "api", base.rstrip("/") + "/" + substitute(
            url, {"id": item_id}).lstrip("/")
    return None, "no detail route in config"


def reader_target(cfg, item_id, chapter_id=None):
    """Return (kind, url, extra) for the reader first page, best effort."""
    selectors = ((cfg.get("scraper") or {}).get("selectors") or {})
    reader = selectors.get("reader") or {}
    if api_endpoint(cfg, "images", "pages"):
        url = api_endpoint(cfg, "images", "pages")
        base = (cfg.get("api") or {}).get("url") or cfg.get("baseUrl")
        return "api", base.rstrip("/") + "/" + substitute(
            url, {"id": item_id, "chapter": chapter_id or item_id}).lstrip("/"), {}
    if reader and chapter_id and str(chapter_id).startswith(("http://", "https://")):
        return "html", str(chapter_id), reader
    if reader:
        pattern = reader.get("readerPageUrlPattern")
        if pattern:
            url = substitute(pattern, {"id": item_id, "chapter": chapter_id or item_id})
            return "html", absolute_url(cfg, url), reader
        if chapter_id:
            chapter_url = chapter_target(cfg, chapter_id)
            if chapter_url:
                return "html", chapter_url, reader
        url = pattern_url(cfg, "chapter")
        if url:
            return "html", absolute_url(cfg, substitute(
                url, {"id": item_id})), reader
    return None, "no reader route/selector in config", {}


def chapter_target(cfg, chapter_id):
    for entry in url_patterns(cfg).values():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or ""
        if "{chapter}" in url:
            return absolute_url(cfg, substitute(
                url, {"id": chapter_id, "chapter": chapter_id}))
    url = pattern_url(cfg, "reader") or pattern_url(cfg, "chapter")
    if url:
        return absolute_url(cfg, substitute(url, {"id": chapter_id,
                                                  "chapter": chapter_id}))
    return ""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Fetch:
    def __init__(self, status=None, body=b"", url="", error=None,
                 headers=None, elapsed=0.0):
        self.status = status
        self.body = body
        self.url = url
        self.error = error
        self.headers = headers or {}
        self.elapsed = elapsed

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def ok(self):
        return self.status == 200


def headers_for(cfg, referer=None):
    net = cfg.get("network") or {}
    headers = {"User-Agent": DEFAULT_UA,
               "Accept": "text/html,application/json,application/xhtml+xml,*/*",
               "Accept-Language": "en-US,en;q=0.9",
               "Accept-Encoding": "identity"}
    extra = net.get("headers") or {}
    for key, value in extra.items():
        if isinstance(value, str):
            headers[key] = value
    if referer and "Referer" not in headers:
        headers["Referer"] = referer
    return headers


def http_get(url, headers=None, timeout=20, attempts=2, backoff=2.0):
    """GET with tiny retry (429/5xx/network) and identity encoding."""
    last = Fetch(url=url)
    for attempt in range(1, attempts + 1):
        started = time.time()
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding:
                    raw = gzip.decompress(raw)
                elif "deflate" in encoding:
                    raw = zlib.decompress(raw)
                return Fetch(status=resp.status, body=raw,
                             url=resp.geturl(),
                             headers=dict(resp.headers.items()),
                             elapsed=time.time() - started)
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            last = Fetch(status=exc.code, body=body, url=url,
                         error="HTTP %s" % exc.code,
                         headers=dict(exc.headers.items()) if exc.headers else {},
                         elapsed=time.time() - started)
            retryable = exc.code in (429, 500, 502, 503, 504)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = Fetch(url=url, error=str(getattr(exc, "reason", exc)),
                         elapsed=time.time() - started)
            retryable = True
        if not retryable or attempt >= attempts:
            break
        time.sleep(backoff * attempt)
    return last


def head_image(url, headers=None, timeout=20):
    """Check an image URL without downloading it (Range GET, HEAD fallback)."""
    hdrs = dict(headers or {})
    hdrs["Range"] = "bytes=0-1023"
    hdrs.setdefault("Accept", "image/avif,image/webp,image/*,*/*;q=0.8")
    fetched = http_get(url, hdrs, timeout=timeout, attempts=1)
    return fetched


# ---------------------------------------------------------------------------
# DOM + CSS selector subset
# ---------------------------------------------------------------------------

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}
RAW_TEXT_TAGS = {"script", "style"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text_parts")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.text_parts = []

    def text(self):
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text())
        return html_mod.unescape(" ".join(p for p in parts if p))

    def raw_text(self):
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.raw_text())
        return " ".join(p for p in parts if p)

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v if v is not None else "")
                          for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v if v is not None else "")
                          for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag == tag:
                del self.stack[idx:]
                break

    def handle_data(self, data):
        if self.stack[-1].tag in RAW_TEXT_TAGS:
            return
        if data.strip():
            self.stack[-1].text_parts.append(data)


def parse_html(text):
    builder = _Builder()
    try:
        builder.feed(text or "")
        builder.close()
    except Exception:
        pass
    return builder.root


_ATTR_RE = re.compile(
    r"\[\s*([-\w:]+)\s*(?:(=|\*=|\^=|\$=|\|=|~=)\s*(?:\"([^\"]*)\"|'([^']*)'|([^\]\s]+)))?\s*\]")


def _split_groups(selector):
    groups, buf, depth, quote = [], [], 0, None
    for ch in selector:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            groups.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        groups.append("".join(buf))
    return [g.strip() for g in groups if g.strip()]


def _tokenize(selector):
    """Split one selector group into [(combinator, compound)] tokens."""
    tokens, buf, depth, quote = [], [], 0, None
    combinator = None
    i = 0
    while i < len(selector):
        ch = selector[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in ">+~":
            if buf:
                tokens.append((combinator, "".join(buf).strip()))
                buf = []
            combinator = ch
        elif depth == 0 and ch.isspace():
            if buf:
                tokens.append((combinator, "".join(buf).strip()))
                buf = []
                combinator = " "
        else:
            buf.append(ch)
        i += 1
    if buf:
        tokens.append((combinator, "".join(buf).strip()))
    return [(comb or (" " if idx else None), comp)
            for idx, (comb, comp) in enumerate(tokens)]


_SIMPLE_RE = re.compile(r"^([a-zA-Z][\w-]*|\*)")


def _parse_compound(compound):
    checks = []
    pos = 0
    match = _SIMPLE_RE.match(compound)
    if match:
        tag = match.group(1).lower()
        if tag != "*":
            checks.append(("tag", tag))
        pos = match.end()
    while pos < len(compound):
        ch = compound[pos]
        if ch == "#":
            m = re.match(r"#([\w-]+)", compound[pos:])
            if not m:
                break
            checks.append(("id", m.group(1)))
            pos += m.end()
        elif ch == ".":
            m = re.match(r"\.([\w-]+)", compound[pos:])
            if not m:
                break
            checks.append(("class", m.group(1)))
            pos += m.end()
        elif ch == "[":
            m = _ATTR_RE.match(compound, pos)
            if not m:
                break
            name, op = m.group(1).lower(), m.group(2)
            value = next((g for g in m.groups()[2:] if g is not None), "")
            checks.append(("attr", (name, op, value)))
            pos = m.end()
        elif ch == ":":
            m = re.match(r":([\w-]+)(?:\(([^)]*)\))?", compound[pos:])
            if not m:
                break
            name, arg = m.group(1).lower(), m.group(2)
            if name == "not" and arg:
                checks.append(("not", _parse_compound(arg.strip())))
            elif name == "first-child":
                checks.append(("nth", (0, 1)))
            elif name == "last-child":
                checks.append(("nth-last", (0, 1)))
            elif name == "nth-child" and arg:
                checks.append(("nth", _nth(arg)))
            elif name == "contains" and arg:
                checks.append(("contains", arg.strip("'\" ")))
            # unknown pseudo classes are ignored on purpose
            pos += m.end()
        elif ch == "*":
            pos += 1
        else:
            break
    return checks


def _nth(arg):
    arg = arg.strip().lower()
    if arg == "odd":
        return (1, 2)
    if arg == "even":
        return (0, 2)
    m = re.match(r"^(-?\d+)$", arg)
    if m:
        return (int(m.group(1)) - 1, 1)
    return (0, 1)


def _matches(node, checks):
    for kind, value in checks:
        if kind == "tag" and node.tag != value:
            return False
        if kind == "id" and node.attrs.get("id") != value:
            return False
        if kind == "class" and value not in node.classes():
            return False
        if kind == "attr":
            name, op, expected = value
            if name not in node.attrs:
                return False
            actual = node.attrs.get(name, "")
            if op == "=" and actual != expected:
                return False
            if op == "*=" and expected not in actual:
                return False
            if op == "^=" and not actual.startswith(expected):
                return False
            if op == "$=" and not actual.endswith(expected):
                return False
            if op == "|=" and not (actual == expected
                                   or actual.startswith(expected + "-")):
                return False
            if op == "~=" and expected not in actual.split():
                return False
        if kind == "not" and _matches(node, value):
            return False
        if kind in ("nth", "nth-last"):
            parent = node.parent
            if parent is None:
                return False
            siblings = [c for c in parent.children if c.tag == node.tag]
            if node not in siblings:
                return False
            idx = siblings.index(node)
            if kind == "nth-last":
                idx = len(siblings) - 1 - idx
            offset, step = value
            if idx < offset or (idx - offset) % step:
                return False
        if kind == "contains" and value and value.lower() not in node.text().lower():
            return False
    return True


def _select_tokens(tokens, nodes):
    matched = []
    for node in nodes:
        if _matches(node, tokens[-1][1]) and _match_prefix(node, tokens[:-1]):
            matched.append(node)
    return matched


def _match_prefix(node, tokens):
    if not tokens:
        return True
    combinator, compound = tokens[-1]
    checks = compound
    if combinator == ">":
        parent = node.parent
        return (parent is not None and _matches(parent, checks)
                and _match_prefix(parent, tokens[:-1]))
    if combinator == "+":
        parent = node.parent
        if parent is None:
            return False
        idx = parent.children.index(node)
        return (idx > 0 and _matches(parent.children[idx - 1], checks)
                and _match_prefix(parent.children[idx - 1], tokens[:-1]))
    if combinator == "~":
        parent = node.parent
        if parent is None:
            return False
        idx = parent.children.index(node)
        return any(_matches(sib, checks) and _match_prefix(sib, tokens[:-1])
                   for sib in parent.children[:idx])
    # descendant
    parent = node.parent
    while parent is not None:
        if _matches(parent, checks) and _match_prefix(parent, tokens[:-1]):
            return True
        parent = parent.parent
    return False


def select(root, selector):
    """Return nodes matching a CSS selector (subset), de-duplicated in order."""
    if not selector or not isinstance(selector, str):
        return []
    all_nodes = list(root.iter())
    result = []
    seen = set()
    for group in _split_groups(selector):
        tokens = [(comb, _parse_compound(comp)) for comb, comp in
                  _tokenize(group) if comp]
        if not tokens:
            continue
        for node in _select_tokens(tokens, all_nodes):
            key = id(node)
            if key not in seen:
                seen.add(key)
                result.append(node)
    # preserve document order
    order = {id(n): i for i, n in enumerate(all_nodes)}
    result.sort(key=lambda n: order.get(id(n), 0))
    return result


def count_items(html_text, container):
    if not container:
        return 0
    root = parse_html(html_text)
    return len(select(root, container))


def field_value(node, spec):
    """Extract one field value from a card node using a fieldSpec."""
    if not isinstance(spec, dict):
        return ""
    scope = node
    if spec.get("selector"):
        matches = select(scope, spec["selector"])
        if not matches:
            return ""
        scope = matches[0]
    if spec.get("attribute"):
        value = scope.attrs.get(spec["attribute"], "")
    else:
        value = scope.text()
    if spec.get("regex"):
        try:
            m = re.search(spec["regex"], value)
            value = (m.group(1) if m and m.groups() else
                     (m.group(0) if m else ""))
        except re.error:
            pass
    transform = str(spec.get("transform") or "")
    if transform == "slug":
        value = slugify(value)
    elif transform == "cleanText":
        value = " ".join((value or "").split())
    return (value or "").strip()


def slugify(value):
    """Reduce an href/path to the engine's `transform: slug` last segment."""
    value = (value or "").split("#", 1)[0].split("?", 1)[0].strip()
    while value.endswith("/"):
        value = value[:-1]
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return value


def extract_first_item(html_text, list_block):
    container = (list_block or {}).get("container")
    if not container:
        return None
    root = parse_html(html_text)
    nodes = select(root, container)
    if not nodes:
        return None
    fields = (list_block or {}).get("fields") or {}
    return {
        "index": 0,
        "title": field_value(nodes[0], fields.get("title") or {}),
        "id": field_value(nodes[0], fields.get("id") or {}),
        "coverUrl": field_value(nodes[0], fields.get("coverUrl") or {}),
    }


def extract_images(html_text, reader, selector=None, attribute=None):
    if not html_text:
        return []
    root = parse_html(html_text)
    selector = selector or (reader or {}).get("imageSelector") \
        or ((reader or {}).get("images") or {}).get("selector") \
        or (reader or {}).get("readerImageSelector") \
        or (reader or {}).get("thumbSelector") or ""
    attribute = attribute or (reader or {}).get("readerImageAttr") \
        or ((reader or {}).get("images") or {}).get("attribute") \
        or (reader or {}).get("thumbSrcAttr") or "src"
    urls = []
    for node in select(root, selector):
        value = node.attrs.get(attribute) or node.attrs.get("src") \
            or node.attrs.get("data-src") or ""
        if value:
            urls.append(value)
    return urls


# ---------------------------------------------------------------------------
# JSON (GenericRest) helpers
# ---------------------------------------------------------------------------

def jget(obj, path, default=None):
    if not path:
        return default
    path = path.strip()
    if path.startswith("$"):
        path = path[1:]
    path = path.lstrip(".")
    cur = obj
    for part in [p for p in path.split(".") if p]:
        want_list = part.endswith("[*]")
        part = part[:-3] if want_list else part
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return default
        if want_list:
            if not isinstance(cur, list):
                return default
            return cur
        if cur is None:
            return default
    return cur


def jlist(obj, path):
    """Resolve a JSONPath-ish list; supports a single trailing [*]."""
    if not path:
        return []
    value = jget(obj, path)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def json_item_count(payload, items_path):
    items = jlist(payload, items_path) if items_path else []
    return len(items) if isinstance(items, list) else 0


# ---------------------------------------------------------------------------
# chores
# ---------------------------------------------------------------------------

def jitter_delay(delay, jitter, rng=None):
    rng = rng or random.Random()
    if delay <= 0:
        return 0.0
    spread = delay * (jitter or 0.0)
    return max(0.0, delay + rng.uniform(-spread, spread))


def polite_sleep(seconds):
    if seconds > 0:
        time.sleep(seconds)


def url_host(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
