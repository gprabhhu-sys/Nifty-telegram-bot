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
import re
from datetime import datetime

import feedparser
import matplotlib
matplotlib.use("Agg")  # no display needed, just save to file
import matplotlib.pyplot as plt
import pandas as pd
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


def download_history(symbols, period="1y"):
    """One batch download used by the gainers/losers table AND the three
    native screeners below, so we only hit Yahoo Finance once per run."""
    try:
        return yf.download(
            symbols, period=period, group_by="ticker", threads=True, progress=False
        )
    except Exception as e:
        print("Batch download failed:", e)
        return None


def symbol_frame(data, sym, symbols):
    try:
        df = data[sym] if len(symbols) > 1 else data
        return df.dropna()
    except Exception:
        return None


def get_screener(symbols, data):
    """Top 5 gainers & losers over the latest session."""
    rows = []
    if data is not None:
        for sym in symbols:
            df = symbol_frame(data, sym, symbols)
            if df is None:
                continue
            last, pct = pct_change(df)
            if last is not None:
                rows.append((sym.replace(".NS", ""), last, pct))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:5], sorted(rows, key=lambda r: r[2])[:5]


def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def scan_volume_spike(symbols, data, max_items=10):
    """Volume > 2x its 20-day average, price > Rs 5, above 50 EMA,
    RSI(14) > 55, turnover > Rs 5 crore."""
    hits = []
    for sym in symbols:
        df = symbol_frame(data, sym, symbols)
        if df is None or len(df) < 55:
            continue
        try:
            close, vol = df["Close"], df["Volume"]
            vol_sma20 = vol.rolling(20).mean().iloc[-1]
            ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
            rsi14 = compute_rsi(close, 14).iloc[-1]
            c, v = close.iloc[-1], vol.iloc[-1]
            if (
                v > vol_sma20 * 2
                and c > 5
                and c > ema50
                and rsi14 > 55
                and c * v > 50_000_000
            ):
                hits.append(sym.replace(".NS", ""))
        except Exception:
            continue
    return hits[:max_items]


def scan_swing_expansion(symbols, data, max_items=10):
    """Volume > 1.25x its 20-day EMA, +3% day, decent liquidity."""
    hits = []
    for sym in symbols:
        df = symbol_frame(data, sym, symbols)
        if df is None or len(df) < 25:
            continue
        try:
            close, vol = df["Close"], df["Volume"]
            vol_ema20 = vol.ewm(span=20, adjust=False).mean().iloc[-1]
            close_ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
            c, prev_c, v = close.iloc[-1], close.iloc[-2], vol.iloc[-1]
            if (
                v > vol_ema20 * 1.25
                and c / prev_c >= 1.03
                and close_ema20 * vol_ema20 >= 20_000_000
            ):
                hits.append(sym.replace(".NS", ""))
        except Exception:
            continue
    return hits[:max_items]


def scan_near_52w_high(symbols, data, max_items=10):
    """Within 25% of the 250-day high, above 50 EMA, liquid, no wild single-day move."""
    hits = []
    for sym in symbols:
        df = symbol_frame(data, sym, symbols)
        if df is None or len(df) < 55:
            continue
        try:
            close, vol, high = df["Close"], df["Volume"], df["High"]
            max250 = high.rolling(min(250, len(high)), min_periods=50).max().iloc[-1]
            ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
            vol_ema50 = vol.ewm(span=50, adjust=False).mean().iloc[-1]
            c, prev_c = close.iloc[-1], close.iloc[-2]
            day_change = (c - prev_c) / prev_c * 100
            if (
                c >= max250 * 0.75
                and c >= 30
                and c >= ema50
                and vol_ema50 * c >= 75_000_000
                and -3 <= day_change <= 5
            ):
                hits.append(sym.replace(".NS", ""))
        except Exception:
            continue
    return hits[:max_items]


def get_chartink_results(scan_clause, max_items=10):
    """Pull live results for a scan clause straight from Chartink (used for
    screens needing fundamentals data we can't reliably source elsewhere).
    Unofficial endpoint - can break if Chartink changes their site."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    try:
        session = requests.Session()
        page = session.get("https://chartink.com/screener/process", headers=headers, timeout=15)
        match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
        if not match:
            return []
        headers["x-csrf-token"] = match.group(1)
        resp = session.post(
            "https://chartink.com/screener/process",
            headers=headers,
            data={"scan_clause": scan_clause},
            timeout=20,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        return [row.get("nsecode") or row.get("name") or "?" for row in rows[:max_items]]
    except Exception as e:
        print("Chartink fetch failed:", e)
        return []


CANSLIM_SCAN_CLAUSE = (
    '( {cash} ( quarterly eps after extraordinary items basic > 4 quarters ago '
    'eps after extraordinary items basic and quarterly net sales > 4 quarters '
    'ago net sales and quarterly net profit/reported profit after tax > 4 '
    'quarters ago net profit/reported profit after tax and daily close > daily '
    'sma ( daily close , 200 ) and daily sma ( daily close , 50 ) > daily sma ( '
    'daily close , 200 ) and market cap > 300 and daily volume > daily sma ( '
    'daily volume , 20 ) and yearly return on net worth percentage > 15 and '
    'yearly return on capital employed percentage > 15 and yearly debt equity '
    'ratio < 1.5 and daily cci ( 34 ) > 100 ) )'
)


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
    data = download_history(symbols, period="1y")

    gainers, losers = get_screener(symbols, data)
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

    if data is not None:
        vol_spike = scan_volume_spike(symbols, data)
        if vol_spike:
            lines.append("<b>\U0001F4E2 Volume Spike Scanner</b>")
            lines.append(", ".join(html.escape(s) for s in vol_spike))
            lines.append("")

        swing_exp = scan_swing_expansion(symbols, data)
        if swing_exp:
            lines.append("<b>\U0001F680 Swing Volume Expansion</b>")
            lines.append(", ".join(html.escape(s) for s in swing_exp))
            lines.append("")

        near_high = scan_near_52w_high(symbols, data)
        if near_high:
            lines.append("<b>\U0001F3AF Near 52-Week High</b>")
            lines.append(", ".join(html.escape(s) for s in near_high))
            lines.append("")

    canslim = get_chartink_results(CANSLIM_SCAN_CLAUSE)
    if canslim:
        lines.append("<b>\U0001F4AA Prabhu Swing Trade (Quality + Momentum)</b>")
        lines.append(", ".join(html.escape(s) for s in canslim))
        lines.append("")

    news = get_news()
    if news:
        lines.append("<b>Market News</b>")
        for headline in news:
            lines.append(f"\u2022 {html.escape(headline)}")

    return "\n".join(lines).strip()


def generate_index_chart(path="nifty_chart.png"):
    """Draw a simple 1-month line chart of the Nifty 50 index and save as PNG."""
    try:
        hist = yf.Ticker("^NSEI").history(period="1mo")
        if hist.empty:
            return None
        up = hist["Close"].iloc[-1] >= hist["Close"].iloc[0]
        color = "#1a9e46" if up else "#d9362a"

        plt.figure(figsize=(8, 4.5))
        plt.plot(hist.index, hist["Close"], color=color, linewidth=2)
        plt.fill_between(hist.index, hist["Close"], hist["Close"].min(), color=color, alpha=0.08)
        plt.title("NIFTY 50 \u2014 Last 1 Month", fontsize=13, fontweight="bold")
        plt.ylabel("Close (\u20b9)")
        plt.grid(alpha=0.25)
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close()
        return path
    except Exception as e:
        print("Chart generation failed:", e)
        return None


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


def send_telegram_photo(photo_path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
            files={"photo": f},
            timeout=60,
        )
    if not resp.ok:
        print("Telegram photo error:", resp.text)
    resp.raise_for_status()


if __name__ == "__main__":
    msg = build_message() or "No market data available today."
    if len(msg) > 4000:  # Telegram's hard limit is 4096 chars
        msg = msg[:4000] + "\n\n\u2026(truncated)"
    send_telegram(msg)

    chart_path = generate_index_chart()
    if chart_path:
        send_telegram_photo(chart_path, caption="NIFTY 50 \u2014 1 Month chart")

    print("Report sent successfully.")
