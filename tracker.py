#!/usr/bin/env python3

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import base64


def _d(s):
    return base64.b64decode(s).decode("utf-8")


_HOST = _d("aHR0cHM6Ly9hcGkudGVsZWdyYW0ub3JnL2JvdA==")

STATE_PATH = Path(__file__).with_name("state.json")
HISTORY_PATH = Path(__file__).with_name("history.csv")
HISTORY_FIELDS = [
    "timestamp", "count",
    "min_all", "min_qual",
    "eff_min_all", "eff_min_qual",
    "rec_price", "rec_eff",
]

HISTORY_RETENTION_DAYS = 365*10

DEFAULT_OFFERS_URL = _d("aHR0cHM6Ly93d3cuZWxkb3JhZG8uZ2cvYXBpL3ByZWRlZmluZWRPZmZlcnMvYXVnbWVudGVkR2FtZS9vZmZlcnM/Z2FtZUlkPTcwJmNhdGVnb3J5PUN1cnJlbmN5JnBhZ2VJbmRleD0xJnBhZ2VTaXplPTE1MA==")
DEFAULT_PAGE_URL = _d("aHR0cHM6Ly93d3cuZWxkb3JhZG8uZ2cvYnV5LXJvYnV4L2cvNzAtMC0w")
DEFAULT_TOPOFFER_URL = _d("aHR0cHM6Ly93d3cuZWxkb3JhZG8uZ2cvYXBpL3ByZWRlZmluZWRPZmZlcnMvYXVnbWVudGVkR2FtZS90b3BPZmZlcj9nYW1lSWQ9NzAmY2F0ZWdvcnk9Q3VycmVuY3kmcGFnZVNpemU9MQ==")
MAX_PAGES = 20

TAX_RATE = 0.30
FEE_RATE = 0.08
FEE_FLAT = 0.30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_CONFIG = {
    "max_price_per_1k": 5.0,
    "max_min_order": 1000,
    "enabled": True,
}




def _targets():
    raw = os.environ.get("NOTIFY_TARGET", "").strip()
    out = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            dec = base64.b64decode(p).decode("utf-8").strip()
            if dec and dec.lstrip("-").isdigit():
                p = dec
        except Exception:
            pass
        out.append(p)
    return out


def load_state():
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            state = {}
    else:
        state = {}
    state.setdefault("config", {})
    for key, value in DEFAULT_CONFIG.items():
        state["config"].setdefault(key, value)
    state.setdefault("update_cursor", 0)
    state.setdefault("alerted", {})
    state["chat_ids"] = _targets()
    return state


def save_state(state):
    to_write = {k: v for k, v in state.items() if k != "chat_ids"}
    STATE_PATH.write_text(json.dumps(to_write, indent=2, ensure_ascii=False), "utf-8")




def nt_token():
    token = os.environ.get("NOTIFY_TOKEN", "").strip()
    if not token:
        sys.exit("NOTIFY_TOKEN is not set.")
    return token


def nt_call(method, **params):
    url = _HOST + nt_token() + "/" + method
    try:
        resp = requests.post(url, json=params, timeout=30)
        return resp.json()
    except requests.RequestException as exc:
        print(f"messenger {method} failed: {exc}")
        return {"ok": False}


def nt_send(chat_id, text):
    nt_call(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def nt_send_photo(chat_id, png_bytes, caption=""):
    url = _HOST + nt_token() + "/sendPhoto"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("history.png", png_bytes, "image/png")},
            timeout=60,
        )
        return resp.json()
    except requests.RequestException as exc:
        print(f"messenger sendPhoto failed: {exc}")
        return {"ok": False}


def nt_get_updates(offset):
    res = nt_call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
    return res.get("result", []) if res.get("ok") else []



HELP_TEXT = _d("PGI+dW5pdHMgcHJpY2UgdHJhY2tlcjwvYj4KSSB3YXRjaCBzb3VyY2UuZ2cgYW5kIHBpbmcgeW91IHdoZW4gYSBtYXRjaGluZyBvZmZlciBhcHBlYXJzLgoKPGI+Q29tbWFuZHM8L2I+Ci9zZXRwcmljZSAmbHQ7dXNkJmd0OyAtIGFsZXJ0IHdoZW4gcHJpY2UgcGVyIDEsMDAwIHVuaXRzIGlzIGF0IG9yIGJlbG93IHRoaXMKL3NldG1pbm9yZGVyICZsdDt1bml0cyZndDsgLSBvbmx5IG9mZmVycyB3aG9zZSBzZWxsZXIgbWluIG9yZGVyIGlzIGF0IG9yIGJlbG93IHRoaXMKL3N0YXR1cyAtIHNob3cgY3VycmVudCBzZXR0aW5ncyBhbmQgdGhlIGJlc3QgbWF0Y2hpbmcgb2ZmZXIgcmlnaHQgbm93Ci9iZXN0IC0gbGlzdCB0aGUgY2hlYXBlc3Qgb2ZmZXJzIHdpdGhpbiB5b3VyIG1pbi1vcmRlciBmaWx0ZXIKL3JlY29tbWVuZGVkIC0gc2hvdyBzb3VyY2UncyBjdXJyZW50IHJlY29tbWVuZGVkICh0b3ApIG9mZmVyCi9ncmFwaCBbcmFuZ2VdIFtsaW5lc10gLSBwcmljZSBoaXN0b3J5IGNoYXJ0IChzZWUgL2dyYXBoaGVscCkKL2VuYWJsZSAtIHJlc3VtZSBhbGVydHMKL2Rpc2FibGUgLSBwYXVzZSBhbGVydHMKL2hlbHAgLSBzaG93IHRoaXMgbWVzc2FnZQ==")

GRAPH_HELP = _d("PGI+L2dyYXBoPC9iPiAtIHByaWNlIGhpc3RvcnkgY2hhcnQKCjxiPlJhbmdlPC9iPiAoZGVmYXVsdCA3ZCk6Ci0gPGNvZGU+MjRoPC9jb2RlPiwgPGNvZGU+N2Q8L2NvZGU+LCA8Y29kZT4ydzwvY29kZT4sIDxjb2RlPjkwZDwvY29kZT4gLSBsYXN0IE4gaG91cnMvZGF5cy93ZWVrcwotIDxjb2RlPmFsbDwvY29kZT4gLSBldmVyeXRoaW5nIHJlY29yZGVkCi0gPGNvZGU+MjAyNi0wNi0wMS4uMjAyNi0wNi0yMDwvY29kZT4gLSBleHBsaWNpdCBkYXRlIHJhbmdlCgo8Yj5MaW5lczwvYj4gKGJvdGggc2hvd24gYnkgZGVmYXVsdCk6Ci0gPGNvZGU+bWluPC9jb2RlPiAtIGNoZWFwZXN0IHByaWNlIGxpbmUgb25seQotIDxjb2RlPnJlYzwvY29kZT4gKG9yIDxjb2RlPnRvcDwvY29kZT4pIC0gc291cmNlJ3MgcmVjb21tZW5kZWQtb2ZmZXIgbGluZSBvbmx5Ci0gPGNvZGU+bm9taW48L2NvZGU+IC8gPGNvZGU+bm9yZWM8L2NvZGU+IC0gaGlkZSBhIGxpbmUKLSA8Y29kZT5ub3RocmVzaG9sZDwvY29kZT4gLSBoaWRlIHlvdXIgYWxlcnQtcHJpY2UgbGluZQotIDxjb2RlPnF1YWw8L2NvZGU+IC0gcmVzdHJpY3QgdGhlIGNoZWFwZXN0IGxpbmUgdG8geW91ciBtaW4tb3JkZXIgZmlsdGVyCi0gPGNvZGU+ZWZmPC9jb2RlPiAob3IgPGNvZGU+cmVhbDwvY29kZT4pIC0gc2hvdyByZWFsIGNvc3QgYWZ0ZXIgMzAlIHBsYXRmb3JtIHRheCArIHNvdXJjZSBmZWVzCgo8Yj5FeGFtcGxlczwvYj4KPGNvZGU+L2dyYXBoIDMwZDwvY29kZT4KPGNvZGU+L2dyYXBoIDI0aCBtaW48L2NvZGU+Cjxjb2RlPi9ncmFwaCBhbGwgbm9yZWMgcXVhbDwvY29kZT4KPGNvZGU+L2dyYXBoIDkwZCBlZmY8L2NvZGU+Cjxjb2RlPi9ncmFwaCAyMDI2LTA2LTAxLi4yMDI2LTA2LTIwPC9jb2RlPg==")


def fmt_config(cfg):
    return (
        f"Price threshold: <b>${cfg['max_price_per_1k']:.4f}</b> per 1,000 units\n"
        f"Max seller min order: <b>{cfg['max_min_order']:,} units</b>\n"
        f"Alerts: <b>{'on' if cfg['enabled'] else 'paused'}</b>"
    )


def parse_command(text):
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None, []
    cmd = parts[0].split("@", 1)[0].lower()
    return cmd, parts[1:]


def process_commands(state, offers):
    cfg = state["config"]
    allowed = set(state["chat_ids"])
    changed = False
    updates = nt_get_updates(state["update_cursor"])

    for upd in updates:
        state["update_cursor"] = upd["update_id"] + 1
        msg = upd.get("message") or {}
        text = msg.get("text", "")
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        if not chat_id or not text:
            continue
        if allowed and chat_id not in allowed:
            continue

        cmd, args = parse_command(text)
        if cmd is None:
            continue

        if cmd in ("/start", "/help"):
            nt_send(chat_id, HELP_TEXT + "\n\n" + fmt_config(cfg))

        elif cmd == "/setprice":
            try:
                cfg["max_price_per_1k"] = round(float(args[0].replace("$", "")), 4)
                changed = True
                nt_send(chat_id, "\u2705 " + fmt_config(cfg))
            except (IndexError, ValueError):
                nt_send(chat_id, "Usage: /setprice 4.80")

        elif cmd == "/setminorder":
            try:
                cfg["max_min_order"] = int(float(args[0].replace(",", "")))
                changed = True
                nt_send(chat_id, "\u2705 " + fmt_config(cfg))
            except (IndexError, ValueError):
                nt_send(chat_id, "Usage: /setminorder 1000")

        elif cmd in ("/enable", "/resume"):
            cfg["enabled"] = True
            changed = True
            nt_send(chat_id, "\u2705 Alerts resumed.\n" + fmt_config(cfg))

        elif cmd in ("/disable", "/pause"):
            cfg["enabled"] = False
            changed = True
            nt_send(chat_id, "\u23f8 Alerts paused.")

        elif cmd == "/status":
            matching = matching_offers(offers, cfg)
            if matching:
                nt_send(chat_id, fmt_config(cfg) + "\n\nBest match now:\n" + fmt_offer(matching[0]))
            else:
                nt_send(chat_id, fmt_config(cfg) + "\n\nNo offer matches right now.")

        elif cmd == "/best":
            within = [o for o in offers if o["min_qty"] <= cfg["max_min_order"]]
            within.sort(key=lambda o: o["price_per_1k"])
            if within:
                lines = [fmt_offer(o) for o in within[:5]]
                nt_send(chat_id, "Cheapest offers within your min-order filter:\n\n" + "\n\n".join(lines))
            else:
                nt_send(chat_id, "No offers found within your min-order filter.")

        elif cmd in ("/recommended", "/rec", "/top"):
            top = fetch_top_offer()
            if top:
                nt_send(
                    chat_id,
                    "\u2b50 source's recommended offer (its optimal pick by "
                    "price, rating, trust &amp; delivery):\n\n" + fmt_offer(top),
                )
            else:
                nt_send(chat_id, "Couldn't fetch the recommended offer right now.")

        elif cmd in ("/graphhelp", "/helpgraph"):
            nt_send(chat_id, GRAPH_HELP)

        elif cmd == "/graph":
            handle_graph(chat_id, args, cfg)

        else:
            nt_send(chat_id, "Unknown command.\n\n" + HELP_TEXT)

    return changed




def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        return float(m.group()) if m else None
    if isinstance(value, dict):
        for k in ("amount", "value", "usd", "USD"):
            if k in value:
                return _num(value[k])
    return None


def _fmt_delivery(expected):
    if not expected or not isinstance(expected, str):
        return ""
    days = 0
    clock = expected
    if "." in expected and expected.split(".")[0].isdigit():
        days_str, clock = expected.split(".", 1)
        days = int(days_str)
    try:
        h, m, s = (int(float(x)) for x in clock.split(":"))
    except ValueError:
        return expected
    if days:
        return f"~{days}d"
    if h:
        return f"~{h}h" + (f"{m}m" if m else "")
    return f"~{m}m"


def price_per_1k(price_per_unit):
    if price_per_unit is None or price_per_unit <= 0:
        return None
    return round(price_per_unit * 1000, 4)


def effective_price_per_1k(listed_ppk, delivery_method, order_qty):
    if listed_ppk is None or listed_ppk <= 0:
        return None
    method = re.sub(r"[^a-z]", "", (delivery_method or "").lower())
    taxed = ("group" not in method) and ("ingame" not in method)
    keep = (1.0 - TAX_RATE) if taxed else 1.0
    qty = order_qty if order_qty and order_qty > 0 else 1000
    eff = (listed_ppk * (1.0 + FEE_RATE)) / keep
    eff += (FEE_FLAT * 1000.0) / (keep * qty)
    return round(eff, 4)


def parse_source_offers(data):
    if not isinstance(data, dict):
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []
    out = []
    for row in results:
        if not isinstance(row, dict):
            continue
        offer = row.get("offer") or {}
        if offer.get("offerState") not in (None, "Active"):
            continue
        price = _num(offer.get("pricePerUnitInUSD") or offer.get("pricePerUnit"))
        ppk = price_per_1k(price)
        if ppk is None:
            continue
        user = row.get("user") or {}
        order_info = row.get("userOrderInfo") or {}
        delivery = row.get("deliveryTime") or {}
        min_qty = int(_num(offer.get("minQuantity")) or 0)
        stock = int(_num(offer.get("quantity")) or 0)
        out.append(
            {
                "id": str(offer.get("id") or f"{user.get('username')}:{price}:{min_qty}"),
                "seller": str(user.get("username") or "seller"),
                "verified": bool(user.get("isVerifiedSeller")),
                "rating": _num(order_info.get("feedbackScore")),
                "rating_count": int(_num(order_info.get("ratingCount")) or 0),
                "price_per_unit": round(ppk / 1000, 6),
                "price_per_1k": ppk,
                "eff_per_1k": effective_price_per_1k(
                    ppk, offer.get("deliveryMethod"), min_qty
                ),
                "min_qty": min_qty,
                "stock": stock,
                "delivery": _fmt_delivery(delivery.get("expectedTime")),
                "delivery_method": str(offer.get("deliveryMethod") or ""),
            }
        )
    return out


def _with_page(url, page):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["pageIndex"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def fetch_offers():
    base = os.environ.get("SOURCE_URL", "").strip() or DEFAULT_OFFERS_URL
    offers = []
    try:
        page = 1
        while page <= MAX_PAGES:
            resp = requests.get(_with_page(base, page), headers=HEADERS, timeout=40)
            resp.raise_for_status()
            data = resp.json()
            batch = parse_source_offers(data)
            if not batch and page == 1:
                batch = extract_offers(data)
            offers.extend(batch)
            total_pages = data.get("totalPages", 1) if isinstance(data, dict) else 1
            if page >= (total_pages or 1) or not batch:
                break
            page += 1
        if offers:
            return _dedupe(offers)
        print("API returned no recognizable entries; trying page fallback.")
    except (requests.RequestException, ValueError) as exc:
        print(f"API fetch failed ({exc}); trying page fallback.")

    page_url = os.environ.get("SOURCE_PAGE", DEFAULT_PAGE_URL).strip()
    try:
        resp = requests.get(page_url, headers={**HEADERS, "Accept": "text/html"}, timeout=40)
        resp.raise_for_status()
        return _dedupe(extract_offers_from_html(resp.text))
    except requests.RequestException as exc:
        print(f"Page fetch failed: {exc}")
        return []


def _dedupe(offers):
    seen, unique = set(), []
    for o in offers:
        if o["id"] not in seen:
            seen.add(o["id"])
            unique.append(o)
    return unique



PRICE_KEYS = ["pricePerUnitInUSD", "pricePerUnit", "unitPrice", "price"]
MINQTY_KEYS = ["minQuantity", "minUnitsPerTrade", "minimumQuantity", "minQty", "minOrder"]
STOCK_KEYS = ["quantity", "offerQuantity", "availableQuantity", "stock"]
SELLER_KEYS = ["username", "sellerName", "seller", "userName"]
ID_KEYS = ["id", "offerId", "_id"]


def _first(d, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _looks_like_offer(d):
    if not isinstance(d, dict):
        return False
    return _num(_first(d, PRICE_KEYS)) is not None and (
        _first(d, MINQTY_KEYS) is not None or _first(d, STOCK_KEYS) is not None
    )


def _normalize_generic(raw):
    price = _num(_first(raw, PRICE_KEYS))
    ppk = price_per_1k(price)
    if ppk is None:
        return None
    min_qty = int(_num(_first(raw, MINQTY_KEYS)) or 0)
    seller = _first(raw, SELLER_KEYS) or "seller"
    if isinstance(seller, dict):
        seller = seller.get("name") or seller.get("username") or "seller"
    offer_id = _first(raw, ID_KEYS) or f"{seller}:{price}:{min_qty}"
    return {
        "id": str(offer_id),
        "seller": str(seller),
        "verified": False,
        "rating": None,
        "rating_count": 0,
        "price_per_unit": round(ppk / 1000, 6),
        "price_per_1k": ppk,
        "eff_per_1k": effective_price_per_1k(ppk, "", min_qty),
        "min_qty": min_qty,
        "stock": int(_num(_first(raw, STOCK_KEYS)) or 0),
        "delivery": "",
        "delivery_method": "",
    }


def _find_offer_list(node, found):
    if isinstance(node, list):
        if node and sum(_looks_like_offer(x) for x in node) >= max(1, len(node) // 2):
            found.append(node)
        for item in node:
            _find_offer_list(item, found)
    elif isinstance(node, dict):
        for value in node.values():
            _find_offer_list(value, found)


def extract_offers(data):
    candidates = []
    _find_offer_list(data, candidates)
    if not candidates:
        return []
    best = max(candidates, key=len)
    offers = [_normalize_generic(o) for o in best if _looks_like_offer(o)]
    return [o for o in offers if o]


def extract_offers_from_html(html):
    offers = []
    for blob in re.findall(r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)', html, re.DOTALL):
        try:
            text = blob.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            text = blob
        for obj in _scan_json_objects(text):
            offers.extend(parse_source_offers(obj) or extract_offers(obj))
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            offers.extend(parse_source_offers(data) or extract_offers(data))
        except json.JSONDecodeError:
            pass
    return offers


def _scan_json_objects(text):
    out = []
    for start_ch, end_ch in (("[", "]"), ("{", "}")):
        depth, start = 0, None
        for i, ch in enumerate(text):
            if ch == start_ch:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == end_ch and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    chunk = text[start : i + 1]
                    if len(chunk) > 40:
                        try:
                            out.append(json.loads(chunk))
                        except json.JSONDecodeError:
                            pass
                    start = None
    return out



def _tz():
    name = os.environ.get("SAMPLER_TZ", "Europe/Riga")
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _median(values):
    s = sorted(values)
    n = len(s)
    if not n:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def summarize_offers(offers, cfg, top=None):
    prices = [o["price_per_1k"] for o in offers if o.get("price_per_1k")]
    if not prices:
        return None
    qual_offers = [
        o for o in offers
        if o.get("price_per_1k") and o["min_qty"] <= cfg["max_min_order"]
    ]
    qual = [o["price_per_1k"] for o in qual_offers]

    effs = [o["eff_per_1k"] for o in offers if o.get("eff_per_1k")]
    qual_effs = [o["eff_per_1k"] for o in qual_offers if o.get("eff_per_1k")]

    rec_price = round(top["price_per_1k"], 4) if top and top.get("price_per_1k") else ""
    rec_eff = round(top["eff_per_1k"], 4) if top and top.get("eff_per_1k") else ""

    return {
        "count": len(prices),
        "min_all": round(min(prices), 4),
        "min_qual": round(min(qual), 4) if qual else "",
        "eff_min_all": round(min(effs), 4) if effs else "",
        "eff_min_qual": round(min(qual_effs), 4) if qual_effs else "",
        "rec_price": rec_price,
        "rec_eff": rec_eff,
    }


def fetch_top_offer():
    url = os.environ.get("SOURCE_PICK_URL", "").strip() or DEFAULT_TOPOFFER_URL
    try:
        resp = requests.get(url, headers=HEADERS, timeout=40)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Top-entry fetch failed: {exc}")
        return None
    if isinstance(data, dict) and "results" not in data:
        data = {"results": [data]}
    parsed = parse_source_offers(data)
    return parsed[0] if parsed else None


def load_history():
    rows = []
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    return rows


def record_history(offers, cfg, top=None):
    summary = summarize_offers(offers, cfg, top)
    if summary is None:
        return
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = now - timedelta(days=HISTORY_RETENTION_DAYS)

    rows = load_history()
    rows.append({"timestamp": now.isoformat(), **summary})

    kept = []
    for row in rows:
        ts = _parse_ts(row.get("timestamp"))
        if ts is None or ts >= cutoff:
            kept.append(row)

    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        for row in kept:
            writer.writerow({k: row.get(k, "") for k in HISTORY_FIELDS})
    print(f"Recorded history point ({len(kept)} rows retained).")



def parse_graph_args(args):
    toks = [a.strip().lower() for a in args if a.strip()]
    dates = []
    range_name = None
    explicit = set() 
    remove = set() 
    qual = False
    eff = False

    for t in toks:
        candidates = t.split("..") if ".." in t else [t]
        matched_date = False
        for c in candidates:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", c):
                dates.append(c)
                matched_date = True
        if matched_date:
            continue
        if t == "all":
            range_name = "all"
        elif re.match(r"^\d+[hdw]$", t):
            range_name = t
        elif t == "min":
            explicit.add("min")
        elif t in ("rec", "recommended", "top", "best"):
            explicit.add("rec")
        elif t == "nomin":
            remove.add("min")
        elif t in ("norec", "norecommended", "notop", "nobest"):
            remove.add("rec")
        elif t in ("nothreshold", "nothresh", "noline"):
            remove.add("threshold")
        elif t in ("qual", "qualifying", "filtered"):
            qual = True
        elif t in ("eff", "effective", "real", "realcost"):
            eff = True

    show = {"min": True, "rec": True, "threshold": True}
    if explicit:
        show["min"] = "min" in explicit
        show["rec"] = "rec" in explicit
    for r in remove:
        show[r] = False

    tz = _tz()
    now = datetime.now(tz)
    since = until = None
    label = None

    parsed_dates = []
    for d in dates[:2]:
        try:
            parsed_dates.append(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=tz))
        except ValueError:
            pass
    parsed_dates.sort()

    if parsed_dates:
        since = parsed_dates[0]
        if len(parsed_dates) >= 2:
            until = parsed_dates[1] + timedelta(days=1) 
            label = f"{parsed_dates[0]:%b %d} \u2013 {parsed_dates[1]:%b %d}"
        else:
            label = f"since {parsed_dates[0]:%b %d}"
        range_name = "custom"
    elif range_name == "all":
        label = "all time"
    elif range_name:
        n = int(range_name[:-1])
        unit = range_name[-1]
        delta = (
            timedelta(hours=n)
            if unit == "h"
            else timedelta(weeks=n)
            if unit == "w"
            else timedelta(days=n)
        )
        since = now - delta
        label = range_name
    else:
        since = now - timedelta(days=7)
        range_name = "7d"
        label = "7d"

    return {
        "since": since,
        "until": until,
        "range_name": range_name,
        "label": label,
        "show": show,
        "qual": qual,
        "eff": eff,
    }


def render_graph(opts):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    tz = _tz()
    points = []
    for row in load_history():
        ts = _parse_ts(row.get("timestamp"))
        if ts is None:
            continue
        points.append((ts.astimezone(tz), row))
    points.sort(key=lambda p: p[0])

    since, until = opts["since"], opts["until"]
    sel = [
        (ts, row)
        for ts, row in points
        if (since is None or ts >= since) and (until is None or ts <= until)
    ]
    if len(sel) < 2:
        return None

    eff = opts.get("eff")
    if eff:
        min_key = "eff_min_qual" if opts["qual"] else "eff_min_all"
        rec_key = "rec_eff"
    else:
        min_key = "min_qual" if opts["qual"] else "min_all"
        rec_key = "rec_price"
    suffix = " (within min-order)" if opts["qual"] else ""
    cost_tag = " real cost" if eff else ""

    def series(key):
        xs, ys = [], []
        for ts, row in sel:
            raw = row.get(key, "")
            if raw in ("", None):
                continue
            try:
                y = float(raw)
            except (TypeError, ValueError):
                continue
            if y <= 0 or y > 1000:
                continue
            ys.append(y)
            xs.append(ts)
        return xs, ys

    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=130)
    plotted = False

    if opts["show"]["min"]:
        xs, ys = series(min_key)
        if xs:
            ax.plot(xs, ys, color="#2e7d32", linewidth=2.0, label="Cheapest" + cost_tag + suffix)
            plotted = True
    if opts["show"]["rec"]:
        xs, ys = series(rec_key)
        if xs:
            ax.plot(xs, ys, color="#ef6c00", linewidth=1.8, label="source pick" + cost_tag)
            plotted = True
    threshold = None if eff else opts.get("threshold")
    if opts["show"]["threshold"] and threshold is not None:
        ax.axhline(
            threshold,
            color="#c62828",
            linestyle="--",
            linewidth=1.2,
            label=f"Alert threshold ${threshold:.2f}",
        )
        plotted = True

    if not plotted:
        plt.close(fig)
        return None

    ax.set_title(
        f"units {'real-cost' if eff else 'price'} history \u00b7 {opts['label']}"
    )
    ax.set_ylabel("USD per 1,000 units" + (" (after tax + fees)" if eff else ""))
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"${v:,.2f}"))
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=tz))
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)

    last = sel[-1][1]

    def latest(key):
        raw = last.get(key, "")
        try:
            return f"${float(raw):.4f}"
        except (TypeError, ValueError):
            return "n/a"

    title = "units real-cost history" if eff else "units price history"
    cap = [f"<b>{title}</b> \u00b7 {opts['label']}"]
    tag = " real cost" if eff else ""
    if opts["show"]["min"]:
        cap.append(f"Cheapest{tag} now: {latest(min_key)}")
    if opts["show"]["rec"]:
        cap.append(f"source pick{tag} now: {latest(rec_key)}")
    if eff:
        cap.append("Real cost = listed + 30% platform tax + 8% +$0.30 fee")
    cap.append(f"{len(sel)} data points")
    return buf.getvalue(), "\n".join(cap)


def handle_graph(chat_id, args, cfg):
    opts = parse_graph_args(args)
    opts["threshold"] = cfg["max_price_per_1k"]
    try:
        result = render_graph(opts)
    except Exception as exc: 
        print(f"Graph render failed: {exc}")
        nt_send(chat_id, "Sorry, I couldn't render that graph. Try a different range.")
        return
    if result is None:
        nt_send(
            chat_id,
            "Not enough price history for that range yet. I record a point every run, "
            "so check back after a while \u2014 or try <code>/graph all</code>.",
        )
        return
    png, caption = result
    nt_send_photo(chat_id, png, caption=caption)




def matching_offers(offers, cfg):
    matches = [
        o for o in offers
        if o["min_qty"] <= cfg["max_min_order"]
        and o["stock"] >= cfg["max_min_order"]
        and o["price_per_1k"] <= cfg["max_price_per_1k"]
    ]
    matches.sort(key=lambda o: o["price_per_1k"])
    return matches


def fmt_offer(o):
    page = os.environ.get("SOURCE_PAGE", DEFAULT_PAGE_URL)
    extras = []
    if o.get("delivery"):
        extras.append(o["delivery"])
    if o.get("delivery_method"):
        extras.append(o["delivery_method"])
    if o.get("verified"):
        extras.append("\u2713 verified")
    if o.get("rating"):
        extras.append(f"{o['rating']:.1f}% ({o['rating_count']:,})")
    meta = (" \u00b7 " + " \u00b7 ".join(extras)) if extras else ""
    eff = o.get("eff_per_1k")
    eff_line = (
        f"\u2192 <b>~${eff:.4f}</b> / 1,000 real cost (after 30% tax + fees)\n"
        if eff
        else ""
    )
    return (
        f"\U0001f4b0 <b>${o['price_per_1k']:.4f}</b> / 1,000 units listed "
        f"(${o['price_per_unit']:.5f}/unit)\n"
        f"{eff_line}"
        f"Seller: {o['seller']}{meta}\n"
        f"Min order: {o['min_qty']:,} \u00b7 Stock: {o['stock']:,}\n"
        f'<a href="{page}">Open on source</a>'
    )


def evaluate(state, offers):
    cfg = state["config"]
    if not cfg["enabled"]:
        print("Alerts paused; skipping notifications.")
        return
    if not state["chat_ids"]:
        print("No registered destinations yet.")
        return

    matches = matching_offers(offers, cfg)
    new_alerted = {}
    to_notify = []
    for o in matches:
        prev = state["alerted"].get(o["id"])
        if prev is None or o["price_per_1k"] < prev - 1e-9:
            to_notify.append(o)
        new_alerted[o["id"]] = o["price_per_1k"]

    state["alerted"] = new_alerted

    if not to_notify:
        print(f"{len(matches)} matching entries(s), nothing new to alert.")
        return

    header = f"\U0001f6a8 {len(to_notify)} units offer(s) match your filters:"
    body = "\n\n".join(fmt_offer(o) for o in to_notify[:5])
    if len(to_notify) > 5:
        body += f"\n\n\u2026and {len(to_notify) - 5} more."
    message = header + "\n\n" + body
    for chat_id in state["chat_ids"]:
        nt_send(chat_id, message)
    print(f"Sent {len(to_notify)} alert(s).")



def main():
    state = load_state()
    offers = fetch_offers()
    print(f"Fetched {len(offers)} entries(s).")
    top = fetch_top_offer()
    if top:
        print(f"{top['price_per_1k']:.4f}/1000.")

    if offers:
        record_history(offers, state["config"], top)

    process_commands(state, offers)

    if offers:
        evaluate(state, offers)
    else:
        print("No data fetched. Check SOURCE_URL.")

    save_state(state)


if __name__ == "__main__":
    main()
