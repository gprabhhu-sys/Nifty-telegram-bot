"""
Daily Indian Markets report -> Telegram.

Fetches:
  - Nifty 50 / Sensex / Bank Nifty index levels
  - Top 5 gainers & losers within the Nifty 50 basket
  - Latest market/business news headlines (RSS)

Then sends one formatted message to your Telegram chat via a bot.

Env vars required (set as GitHub Secrets, see README.md):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import csv
import html
import io
import os
from datetime import datetime

import feedparser
import requests
import yfinance as yf

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Used only if the live Nifty 500 list can't be fetched (NSE site hiccup etc).
FALLBACK_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "BAJFINANCE.NS",
    "KOTAKBANK.NS", "LT.NS", "HCLTECH.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS", "ULTRACEMCO.NS", "NESTLEIND.NS",
    "WIPRO.NS", "M&M.NS", "NTPC.NS", "POWERGRID.NS", "ADANIENT.NS",
    "ADANIPORTS.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "TATAMOTORS.NS", "ONGC.NS",
    "COALINDIA.NS", "BAJAJFINSV.NS", "INDUSINDBK.NS", "GRASIM.NS", "HDFCLIFE.NS",
    "SBILIFE.NS", "DIVISLAB.NS", "DRREDDY.NS", "CIPLA.NS", "EICHERMOT.NS",
    "BPCL.NS", "BRITANNIA.NS", "HEROMOTOCO.NS", "APOLLOHOSP.NS", "TECHM.NS",
    "UPL.NS", "BAJAJ-AUTO.NS", "HINDALCO.NS", "SHRIRAMFIN.NS", "TRENT.NS",
]

NIFTY500_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"


def get_nifty500_symbols():
    """Fetch the current Nifty 500 constituent list live from NSE.

    NSE's site blocks plain requests without a browser-like session, so we
    first hit the homepage to pick up cookies, then request the CSV.
    Falls back to a small static list if anything goes wrong.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/csv,application/vnd.ms-excel,*/*",
    }
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=15)
        resp = session.get(NIFTY500_CSV_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        symbols = [
            row["Symbol"].strip() + ".NS"
            for row in reader
            if row.get("Symbol") and "DUMMY" not in row["Symbol"].upper()
        ]
        if len(symbols) > 100:
            return symbols
    except Exception:
        pass
    return FALLBACK_SYMBOLS

INDICES = {"NIFTY 50": "^NSEI", "SENSEX": "^BSESN", "BANK NIFTY": "^NSEBANK"}

NEWS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://www.moneycontrol.com/rss/business.xml",
    "https://www.livemint.com/rss/markets",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
    "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
]


def pct_change(hist):
    if len(hist) < 2:
        return None, None
    prev_close = hist["Close"].iloc[-2]
    last_close = hist["Close"].iloc[-1]
    return last_close, (last_close - prev_close) / prev_close * 100


def get_indices():
    out = []
    for name, sym in INDICES.items():
        try:
            hist = yf.Ticker(sym).history(period="5d")
            last, pct = pct_change(hist)
            if last is not None:
                out.append((name, last, pct))
        except Exception:
            continue
    return out


def get_screener(symbols):
    """Batch-download recent prices for all symbols at once (fast, avoids
    hammering Yahoo Finance with 500 individual requests)."""
    rows = []
    try:
        data = yf.download(
            symbols, period="5d", group_by="ticker", threads=True, progress=False
        )
    except Exception:
        data = None

    if data is not None:
        for sym in symbols:
            try:
                hist = data[sym] if len(symbols) > 1 else data
                hist = hist.dropna()
                last, pct = pct_change(hist)
                if last is not None:
                    rows.append((sym.replace(".NS", ""), last, pct))
            except Exception:
                continue

    rows.sort(key=lambda r: r[2], reverse=True)
    top_gainers = rows[:5]
    top_losers = sorted(rows, key=lambda r: r[2])[:5]
    return top_gainers, top_losers


def get_news(max_items=10):
    items = []
    for url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                items.append(entry.title.strip())
        except Exception:
            continue
    seen, unique = set(), []
    for title in items:
        if title not in seen:
            unique.append(title)
            seen.add(title)
    return unique[:max_items]


def fmt_pct(p):
    arrow = "\U0001F7E2" if p >= 0 else "\U0001F534"  # green / red circle
    return f"{arrow} {p:+.2f}%"


def build_message():
    today = datetime.now().strftime("%d %b %Y")
    lines = [f"<b>Indian Markets Daily \u2014 {today}</b>", ""]

    indices = get_indices()
    if indices:
        lines.append("<b>Indices</b>")
        for name, last, pct in indices:
            lines.append(f"{html.escape(name)}: {last:,.2f}  {fmt_pct(pct)}")
        lines.append("")

    symbols = get_nifty500_symbols()
    gainers, losers = get_screener(symbols)
    if gainers:
        lines.append("<b>Top Gainers (Nifty 500)</b>")
        for sym, price, pct in gainers:
            lines.append(f"{html.escape(sym)}: \u20b9{price:,.2f}  {fmt_pct(pct)}")
        lines.append("")
    if losers:
        lines.append("<b>Top Losers (Nifty 500)</b>")
        for sym, price, pct in losers:
            lines.append(f"{html.escape(sym)}: \u20b9{price:,.2f}  {fmt_pct(pct)}")
        lines.append("")

    news = get_news()
    if news:
        lines.append("<b>Market News</b>")
        for headline in news:
            lines.append(f"\u2022 {html.escape(headline)}")

    return "\n".join(lines).strip()


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not resp.ok:
        print("Telegram error response:", resp.text)
    resp.raise_for_status()


if __name__ == "__main__":
    msg = build_message() or "No market data available today."
    if len(msg) > 4000:  # Telegram's hard limit is 4096 chars
        msg = msg[:4000] + "\n\n\u2026(truncated)"
    send_telegram(msg)
    print("Report sent successfully.")
