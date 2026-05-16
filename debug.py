#!/usr/bin/env python3
import os, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession, Request as GoogleRequest

# ── 環境変数 ────────────────────────────────────────────
DISCORD_WEBHOOK     = os.environ["DISCORD_WEBHOOK"]
BILLING_ACCOUNT_ID  = os.environ["BILLING_ACCOUNT_ID"]
BUDGET_ID           = os.environ["BUDGET_ID"]
TOTAL_CREDITS       = float(os.environ.get("TOTAL_CREDITS", "10.0"))
ALERT_THRESHOLD_PCT = float(os.environ.get("ALERT_THRESHOLD_PCT", "30"))
GCP_SA_JSON         = os.environ["GCP_SA_JSON"]

STATE_FILE = Path("state.json")

# ── 認証（debug.py と同じ方法）────────────────────────
sa_info = json.loads(GCP_SA_JSON)
creds = service_account.Credentials.from_service_account_info(
    sa_info,
    scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"]
)
creds.refresh(GoogleRequest())
session = AuthorizedSession(creds)

# ── バジェット取得 ──────────────────────────────────────
url = f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT_ID}/budgets/{BUDGET_ID}"
r = session.get(url)
r.raise_for_status()
budget = r.json()

# ── 使用額を計算 ────────────────────────────────────────
def parse_money(m):
    if not m:
        return 0.0
    return float(m.get("units", 0)) + float(m.get("nanos", 0)) / 1_000_000_000

total_spent   = parse_money(budget.get("currentSpend", {}))
remaining     = max(TOTAL_CREDITS - total_spent, 0.0)
remaining_pct = (remaining / TOTAL_CREDITS) * 100 if TOTAL_CREDITS > 0 else 0.0

# ── 今日の使用額（前回との差分）────────────────────────
state = {}
if STATE_FILE.exists():
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        pass

last_spent  = state.get("last_spent", 0.0)
today_spent = max(total_spent - last_spent, 0.0)

jst       = timezone(timedelta(hours=9))
today_str = datetime.now(jst).strftime("%Y-%m-%d")
date_str  = datetime.now(jst).strftime("%-m月%-d日")

STATE_FILE.write_text(json.dumps({"last_spent": total_spent, "last_date": today_str}))

print(f"今日の使用額: ${today_spent:.4f}")
print(f"累計使用額  : ${total_spent:.4f}")
print(f"残高        : ${remaining:.4f} ({remaining_pct:.1f}%)")

# ── Discord に送信 ──────────────────────────────────────
if remaining_pct <= 10:
    color, status = 0xFF0000, "🔴 残量わずか！今すぐチャージを"
elif remaining_pct <= ALERT_THRESHOLD_PCT:
    color, status = 0xFF6600, "🟠 残量が少なくなっています"
else:
    color, status = 0x5865F2, "✅ 問題なし"

bar = "█" * int(remaining_pct / 10) + "░" * (10 - int(remaining_pct / 10))

payload = {
    "embeds": [{
        "title": "Gemini API 日次レポート",
        "description": (
            f"**{date_str} の API 利用まとめ**\n\n"
            f"📅 **今日の使用額:** ${today_spent:.4f}\n"
            f"💸 **累計使用額:** ${total_spent:.4f} / ${TOTAL_CREDITS:.2f}\n\n"
            f"💰 **残高:** ${remaining:.4f}\n"
            f"📊 `{bar}` {remaining_pct:.1f}%\n\n"
            f"{status}"
            + (f"\n\n👉 [GCPコンソールでチャージ](https://console.cloud.google.com/billing)" if remaining_pct <= ALERT_THRESHOLD_PCT else "")
        ),
        "color": color,
        "footer": {"text": "自動チャージはOFF ／ 手動チャージのみ"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]
}

res = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
res.raise_for_status()
print("✅ Discord に送信しました")
