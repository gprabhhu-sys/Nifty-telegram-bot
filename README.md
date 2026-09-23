# Nifty 50 Daily Market Report → Telegram

Sends you a free, automatic Telegram message every weekday morning at **8:00 AM IST** with:
- Nifty 50 / Sensex / Bank Nifty index levels
- Top 5 gainers & losers in the Nifty 50
- Latest market/business news headlines

Runs entirely on **GitHub Actions' free tier** — no server, no laptop needed to be on.

---

## 1. Create your Telegram bot

1. Open Telegram, search for **@BotFather**, and start a chat.
2. Send `/newbot`, give it a name and a username (must end in `bot`, e.g. `my_market_updates_bot`).
3. BotFather replies with a **token** like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxx`. Save it.
4. Send your new bot any message (e.g. "hi") so it can message you back later.

## 2. Get your chat ID

1. In your browser, visit (replace `<TOKEN>` with your bot token):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
2. Look for `"chat":{"id":123456789,...}` in the response — that number is your **chat ID**.
   (If you see nothing, make sure you messaged the bot first, then refresh.)
   - Tip: an easier way is to message **@userinfobot** on Telegram — it instantly replies with your chat ID.

## 3. Create a GitHub repo with these files

1. Create a new **public or private** repo on GitHub (private is fine, Actions works on both for personal accounts within the free minutes quota).
2. Upload these files, keeping the folder structure:
   ```
   main.py
   requirements.txt
   .github/workflows/daily-report.yml
   ```

## 4. Add your secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

Add two secrets:
| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the chat ID you found in step 2 |

## 5. Test it

Go to the **Actions** tab in your repo → **Daily Market Report** workflow → **Run workflow** button (this is the `workflow_dispatch` trigger). Within a minute you should get a message on Telegram.

If it fails, click into the run to see the error log (most common issue: wrong token/chat ID, or a typo in a secret name).

## That's it

From now on it runs automatically every weekday at 8 AM IST — you don't need to keep anything open. GitHub free tier gives 2,000 Action minutes/month for private repos (unlimited for public repos), and this job takes well under a minute a day, so cost is a non-issue.

---

## Customizing

- **Change the stock list**: edit the `NIFTY50` list in `main.py` — add/remove any NSE-listed symbol (use the `.NS` suffix, e.g. `ZOMATO.NS`).
- **Change the time**: edit the `cron` line in `.github/workflows/daily-report.yml`. Cron is in UTC; IST = UTC + 5:30. Example: for 7:30 AM IST use `"0 2 * * 1-5"`.
- **Change news sources**: edit `NEWS_FEEDS` in `main.py` with any RSS feed URL.
- **Add technical filters** (RSI, volume spikes, 52-week highs, etc.): happy to extend `get_screener()` for this — just ask.

## Notes / limitations

- Data comes from Yahoo Finance via `yfinance` — free, no API key, but can occasionally lag or hiccup for NSE tickers; the script skips any symbol it can't fetch rather than failing the whole run.
- This is informational only, not investment advice.
