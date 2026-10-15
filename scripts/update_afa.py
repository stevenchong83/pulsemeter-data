#!/usr/bin/env python3
"""
Keeps the AFA in tariff.json in sync with what Single Buyer publishes.

Rules (same as the Android app's own checker):
  * Only a figure published for the CURRENT calendar month (Malaysia time) is applied.
    A figure already announced for next month is only logged; it is applied on the 1st.
  * Old months are never taken as current, and the stored month never moves backwards.
  * Two sources that disagree -> fail (never guess).
  * A change bigger than MAX_JUMP_SEN -> fail, so a human looks at it.
  * Only afaSen / afaMonth / publishedAt are touched. Everything else in tariff.json stays as you wrote it.

Exit code 0 = fine (updated, or nothing to do). Exit code 1 = needs attention (GitHub e-mails you).

Standard library only. Can also be run by hand:  python3 scripts/update_afa.py
"""
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_URLS = [
    "https://www.singlebuyer.com.my/Generation-Info-Tariff/automatic-fuel-adjustment-%28afa%29",
    "https://www.singlebuyer.com.my/Generation-Info-Tariff/generation-tariff",
]
URLS = [u.strip() for u in os.environ.get("AFA_URLS", "").split(",") if u.strip()] or DEFAULT_URLS
JSON_PATH = os.environ.get("TARIFF_JSON", "tariff.json")
MAX_JUMP_SEN = float(os.environ.get("MAX_JUMP_SEN", "3.0"))
STALE_GRACE_DAYS = int(os.environ.get("STALE_GRACE_DAYS", "2"))
MYT = timezone(timedelta(hours=8))
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 PulseMeterDataBot/1.0"

# ---------------------------------------------------------------- text extraction

def normalize(t):
    t = t.replace("\u00a0", " ").replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    t = re.sub(r"[ \t\x0b\x0c\r]+", " ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def html_to_text(raw):
    s = raw.replace("<![CDATA[", " ").replace("]]>", " ")
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6]|item|title|description)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    return normalize(s)

# ---------------------------------------------------------------- AFA parsing

MONTHS = {
    "january": 1, "januari": 1, "jan": 1, "february": 2, "februari": 2, "feb": 2,
    "march": 3, "mac": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "mei": 5,
    "june": 6, "jun": 6, "july": 7, "julai": 7, "jul": 7, "august": 8, "ogos": 8, "aug": 8, "ogo": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oktober": 10, "oct": 10, "okt": 10,
    "november": 11, "nov": 11, "december": 12, "disember": 12, "dec": 12, "dis": 12,
}
_M = "|".join(sorted(MONTHS, key=len, reverse=True))
KEYWORD = re.compile(r"(?i)\bAFA\b|Automatic\s+Fuel\s+(?:Cost\s+)?Adjustment|Pelarasan\s+Kos\s+Bahan\s+Api")
RATE = re.compile(
    r"(?i)(?:([+\-]?\s?\d{1,2}(?:\.\d{1,4})?)\s*(?:sen|cents?)\s*(?:/|per)\s*kW[hj])"
    r"|(?:RM\s*([+\-]?\d?\.\d{2,4})\s*(?:/|per)\s*kW[hj])"
)
MONTH = re.compile(rf"(?i)\b({_M})\.?\s+(20\d\d)\b")
CONNECTOR = re.compile(r"(?i)\s*(?:for|in|on|during|bagi|untuk|pada)(?:\s+the\s+month\s+of|\s+bulan)?\s*")
NEGATIVE = re.compile(r"(?i)±|\+/-|\bcap(?:ped)?\b|ceiling|threshold|trigger|\bband\b|maximum|\bhad\b|\blimit")
MAX_ABS_RATE = 20.0
MAX_KEYWORD_DIST = 250
MAX_MONTH_DIST = 200


def sentence_context(t, pos, end, reach=120):
    """The sentence/line around a figure (never a neighbouring sentence), at most `reach` chars each side."""
    left = max(t.rfind("\n", 0, pos), t.rfind(". ", 0, pos))
    left = 0 if left < 0 else left + 1
    rights = [i for i in (t.find("\n", end), t.find(". ", end)) if i >= 0]
    right = min(rights) if rights else len(t)
    return t[max(left, pos - reach):min(right, end + reach)]


def find_all(raw_text):
    """-> list of (rate_sen, (year, month), score). Lower score = more convincing."""
    t = normalize(raw_text)
    keywords = [m.start() for m in KEYWORD.finditer(t)]
    if not keywords:
        return []
    months = [(m.start(), m.end() - 1, (int(m.group(2)), MONTHS[m.group(1).lower()])) for m in MONTH.finditer(t)]
    if not months:
        return []
    out = []
    for m in RATE.finditer(t):
        if m.group(1):
            rate = float(m.group(1).replace(" ", ""))
        else:
            rate = float(m.group(2).replace(" ", "")) * 100.0
        if abs(rate) > MAX_ABS_RATE:
            continue
        pos, end = m.start(), m.end()
        kw_dist = min(abs(k - pos) for k in keywords)
        if kw_dist > MAX_KEYWORD_DIST:
            continue
        if NEGATIVE.search(sentence_context(t, pos, end)):
            continue
        following = [x for x in months if 0 <= x[0] - end <= 40 and CONNECTOR.fullmatch(t[end:x[0]])]
        if following:
            chosen = min(following, key=lambda x: x[0])
            month_dist = chosen[0] - end
        else:
            chosen = min(months, key=lambda x: min(abs(x[0] - pos), abs(x[1] - pos)))
            month_dist = min(abs(chosen[0] - pos), abs(chosen[1] - pos))
            if month_dist > MAX_MONTH_DIST:
                continue
        out.append((rate, chosen[2], kw_dist + month_dist))
    return out


def next_month(ym):
    y, m = ym
    return (y + 1, 1) if m == 12 else (y, m + 1)


def select(findings, now_ym):
    """-> (rate, month) | None, ambiguous, latest_month. Preference: this month, next month, newest older month."""
    if not findings:
        return None, False, None
    by = {}
    for rate, ym, score in findings:
        by.setdefault(ym, []).append((rate, score))
    latest = max(by)
    if now_ym in by:
        chosen = now_ym
    elif next_month(now_ym) in by:
        chosen = next_month(now_ym)
    else:
        older = [k for k in by if k < now_ym]
        chosen = max(older) if older else latest
    cands = by[chosen]
    if len({"%.2f" % r for r, _ in cands}) > 1:
        return None, True, latest
    best = min(cands, key=lambda c: c[1])
    return (best[0], chosen), False, latest

# ---------------------------------------------------------------- decision (pure, unit-tested)

def fmt_ym(ym):
    return "%04d-%02d" % ym


def decide(today, old_rate, old_month, readings, fetch_errors, source_count):
    """
    readings: list of (source, picked, ambiguous) where picked = ((rate, ym)) or None.
    Returns (action, message, rate, ym); action in update | nochange | notice | error
    """
    cur = (today.year, today.month)
    if fetch_errors and len(fetch_errors) >= source_count:
        return "error", "No source could be read: " + " | ".join(fetch_errors), None, None

    current = [(s, p) for s, p, _ in readings if p and p[1] == cur]
    upcoming = [(s, p) for s, p, _ in readings if p and p[1] == next_month(cur)]
    ambiguous = [s for s, _, a in readings if a]

    rates = {"%.2f" % p[0] for _, p in current}
    if len(rates) > 1:
        return "error", "Sources disagree on this month's AFA: " + ", ".join(sorted(rates)) + " sen/kWh", None, None

    if current:
        rate = round(current[0][1][0], 2)
        if old_month > cur:
            return "nochange", "tariff.json already holds %s (ahead of %s); left as is" % (fmt_ym(old_month), fmt_ym(cur)), None, None
        if abs(rate - old_rate) > MAX_JUMP_SEN:
            return "error", "AFA moved %.2f -> %.2f sen/kWh (> %.1f). Check Single Buyer and edit tariff.json by hand if correct." % (old_rate, rate, MAX_JUMP_SEN), None, None
        if abs(rate - old_rate) < 0.00005 and old_month == cur:
            return "nochange", "AFA %s = %.2f sen/kWh, already up to date" % (fmt_ym(cur), rate), None, None
        return "update", "AFA %s: %.2f -> %.2f sen/kWh" % (fmt_ym(cur), old_rate, rate), rate, cur

    if upcoming:
        p = upcoming[0][1]
        return "notice", "Next month (%s) is already announced at %.2f sen/kWh; it will be applied on the 1st." % (fmt_ym(p[1]), p[0]), None, None

    if old_month == cur:
        return "notice", "No new figure on the page, but tariff.json already holds %s." % fmt_ym(cur), None, None
    note = " (ambiguous on: %s)" % ", ".join(ambiguous) if ambiguous else ""
    if today.day <= STALE_GRACE_DAYS:
        return "notice", "No AFA for %s published yet%s; waiting (grace period)." % (fmt_ym(cur), note), None, None
    return "error", "No AFA for %s found on any source%s; the page may have changed or the figure is overdue." % (fmt_ym(cur), note), None, None

# ---------------------------------------------------------------- IO

def fetch(url, tries=3, timeout=30, max_bytes=3 * 1024 * 1024):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise RuntimeError("response too large")
                return data.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last))


def gh(kind, msg):
    print("::%s::%s" % (kind, msg))


def set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (key, value))


def main():
    today = datetime.now(MYT).date()
    with open(JSON_PATH, encoding="utf-8") as f:
        pack = json.load(f)
    d = pack["data"]
    old_rate = float(d["afaSen"])
    y, m = d["afaMonth"].split("-")
    old_month = (int(y), int(m))

    readings, errors = [], []
    cur = (today.year, today.month)
    for url in URLS:
        try:
            text = html_to_text(fetch(url))
        except Exception as e:  # noqa: BLE001
            errors.append("%s: %s" % (url, e))
            print("FETCH FAILED", url, e)
            continue
        picked, ambiguous, latest = select(find_all(text), cur)
        print("%s -> %s%s latest=%s" % (url, picked, " AMBIGUOUS" if ambiguous else "", latest))
        readings.append((url, picked, ambiguous))

    action, message, rate, ym = decide(today, old_rate, old_month, readings, errors, len(URLS))
    print("[%s] %s" % (action, message))

    if action == "error":
        gh("error", message)
        set_output("changed", "false")
        return 1
    if action == "notice":
        gh("notice", message)
    if action != "update":
        set_output("changed", "false")
        return 0

    d["afaSen"] = rate
    d["afaMonth"] = fmt_ym(ym)
    pack["publishedAt"] = today.isoformat()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2, ensure_ascii=False)
        f.write("\n")
    set_output("changed", "true")
    set_output("afa_sen", "%.2f" % rate)
    set_output("afa_month", fmt_ym(ym))
    return 0


if __name__ == "__main__":
    sys.exit(main())
