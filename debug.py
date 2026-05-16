#!/usr/bin/env python3
import os
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession, Request as GoogleRequest
from google.cloud import monitoring_v3

# ── 環境変数 ────────────────────────────────────────────
DISCORD_WEBHOOK     = os.environ["DISCORD_WEBHOOK"]
PROJECT_ID          = os.environ["PROJECT_ID"]           # 追加: GCP プロジェクトID
TOTAL_CREDITS       = float(os.environ.get("TOTAL_CREDITS", "10.0"))
ALERT_THRESHOLD_PCT = float(os.environ.get("ALERT_THRESHOLD_PCT", "30"))
GCP_SA_JSON         = os.environ["GCP_SA_JSON"]

STATE_FILE = Path("state.json")

# ── 認証 ────────────────────────────────────────────────
sa_info = json.loads(GCP_SA_JSON)
creds = service_account.Credentials.from_service_account_info(
    sa_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
creds.refresh(GoogleRequest())
session = AuthorizedSession(creds)

# ── Cloud Monitoring から今月の累計使用額を取得 ────────
def get_monthly_cost_since(project_id, start_datetime_utc):
    """指定された日時から現在までの推定コストを返す (USD)"""
    client = monitoring_v3.MetricServiceClient(credentials=creds)
    project_name = f"projects/{project_id}"

    now = datetime.now(timezone.utc)
    interval = monitoring_v3.TimeInterval({
        "start_time": start_datetime_utc,
        "end_time": now,
    })

    # 推定コストのメトリクス (billing/estimated_cost)
    filter_str = 'metric.type="billing/estimated_cost"'

    results = client.list_time_series(
        name=project_name,
        filter=filter_str,
        interval=interval,
        view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
    )

    total = 0.0
    for time_series in results:
        for point in time_series.points:
            total += point.value.double_value
    return total

# 日本時間で今月の1日 00:00:00 を算出
jst = timezone(timedelta(hours=9))
now_jst = datetime.now(jst)
first_day_jst = datetime(now_jst.year, now_jst.month, 1, 0, 0, 0, tzinfo=jst)
first_day_utc = first_day_jst.astimezone(timezone.utc)

total_spent = get_monthly_cost_since(PROJECT_ID, first_day_utc)
remaining = max(TOTAL_CREDITS - total_spent, 0.0)
remaining_pct = (remaining / TOTAL_CREDITS) * 100 if TOTAL_CREDITS > 0 else 0.0

# ── 今日の使用額（前回との差分）────────────────────────
state = {}
if STATE_FILE.exists():
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        pass

last_total = state.get("last_total", 0.0)
today_spent = max(total_spent - last_total, 0.0)

# 状態を保存
today_str = now_jst.strftime("%Y-%m-%d")
STATE_FILE.write_text(json.dumps({"last_total": total_spent, "last_date": today_str}))

print(f"今日の使用額: ${today_spent:.4f}")
print(f"今月の累計  : ${total_spent:.4f}")
print(f"残高        : ${remaining:.4f} ({remaining_pct:.1f}%)")

# ── Discord に送信 ──────────────────────────────────────
if remaining_pct <= 10:
    color, status = 0xFF0000, "🔴 残量わずか！今すぐチャージを"
elif remaining_pct <= ALERT_THRESHOLD_PCT:
    color, status = 0xFF6600, "🟠 残量が少なくなっています"
else:
    color, status = 0x5865F2, "✅ 問題なし"

bar = "█" * int(remaining_pct / 10) + "░" * (10 - int(remaining_pct / 10))

date_str = now_jst.strftime("%-m月%-d日")
payload = {
    "embeds": [{
        "title": "Gemini API 日次レポート",
        "description": (
            f"**{date_str} の API 利用まとめ**\n\n"
            f"📅 **今日の使用額:** ${today_spent:.4f}\n"
            f"💸 **今月の累計:** ${total_spent:.4f} / ${TOTAL_CREDITS:.2f}\n\n"
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
