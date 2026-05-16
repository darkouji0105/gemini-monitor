#!/usr/bin/env python3
"""
Gemini API 毎日レポート＋残量アラート
- 毎晩 9時（JST）に今日の使用額・残高を Discord に送信
- 残量が閾値を下回ったら警告色に変える
- 自動チャージは一切しない
- 「今日の使用額」は前日の累計との差分で計算（state.json にキャッシュ）
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession, Request as GoogleRequest

# ── 環境変数 ────────────────────────────────────────────
DISCORD_WEBHOOK     = os.environ["DISCORD_WEBHOOK"]
BILLING_ACCOUNT_ID  = os.environ["BILLING_ACCOUNT_ID"]  # 例: 012345-ABCDEF-GHIJKL
BUDGET_ID           = os.environ["BUDGET_ID"]
TOTAL_CREDITS       = float(os.environ.get("TOTAL_CREDITS", "10.0"))
ALERT_THRESHOLD_PCT = float(os.environ.get("ALERT_THRESHOLD_PCT", "30"))
GCP_SA_JSON         = os.environ["GCP_SA_JSON"]

# 前日の累計使用額を保存するファイル（GitHub Actions キャッシュで永続化）
STATE_FILE = Path("state.json")


# ── GCP 認証 ────────────────────────────────────────────

def get_credentials():
    sa_info = json.loads(GCP_SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"]
    )
    creds.refresh(GoogleRequest())
    return creds


def get_budget_status(credentials) -> dict:
    session = AuthorizedSession(credentials)
    url = (
        f"https://billingbudgets.googleapis.com/v1/"
        f"billingAccounts/{BILLING_ACCOUNT_ID}/budgets/{BUDGET_ID}"
    )
    res = session.get(url)
    res.raise_for_status()
    return res.json()


def parse_money(money_obj: dict) -> float:
    """Google Money 形式 {"units": "5", "nanos": 500000000} → float"""
    if not money_obj:
        return 0.0
    units = float(money_obj.get("units", 0))
    nanos = float(money_obj.get("nanos", 0)) / 1_000_000_000
    return units + nanos


# ── 状態ファイル（前日の累計を記憶）────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_spent": 0.0, "last_date": ""}


def save_state(spent: float, date_str: str):
    STATE_FILE.write_text(json.dumps({"last_spent": spent, "last_date": date_str}))


# ── Discord 送信 ─────────────────────────────────────────

def send_daily_report(today_spent: float, total_spent: float, remaining: float, remaining_pct: float):
    """毎晩の定時レポートを Discord に送信"""

    if remaining_pct <= 10:
        color  = 0xFF0000
        status = "🔴 残量わずか！手動でチャージしてください"
    elif remaining_pct <= ALERT_THRESHOLD_PCT:
        color  = 0xFF6600
        status = f"🟠 残量 {remaining_pct:.0f}% を下回りました。そろそろチャージを"
    else:
        color  = 0x5865F2  # Discord ブルー（通常）
        status = "✅ 問題なし"

    jst_now  = datetime.now(timezone(timedelta(hours=9)))
    date_str = jst_now.strftime("%-m月%-d日")  # 例: 5月16日

    bar_filled = int(remaining_pct / 10)
    bar_empty  = 10 - bar_filled
    bar        = "█" * bar_filled + "░" * bar_empty

    description = (
        f"**{date_str} の API 利用まとめ**\n\n"
        f"📅 **今日の使用額:** ${today_spent:.4f}\n"
        f"💸 **累計使用額:** ${total_spent:.4f} / ${TOTAL_CREDITS:.2f}\n\n"
        f"💰 **残高:** ${remaining:.4f}\n"
        f"📊 `{bar}` {remaining_pct:.1f}%\n\n"
        f"{status}"
    )

    if remaining_pct <= ALERT_THRESHOLD_PCT:
        description += f"\n\n👉 [GCP コンソールでチャージ](https://console.cloud.google.com/billing)"

    payload = {
        "embeds": [{
            "title": "Gemini API 日次レポート",
            "description": description,
            "color": color,
            "footer": {"text": "自動チャージはOFF ／ 手動チャージのみ"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }

    res = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    res.raise_for_status()
    print("✅ Discord にレポートを送信しました")


# ── メイン ───────────────────────────────────────────────

def main():
    jst       = timezone(timedelta(hours=9))
    jst_now   = datetime.now(jst)
    today_str = jst_now.strftime("%Y-%m-%d")

    print(f"=== Gemini API 日次レポート ({today_str} JST) ===")

    try:
        creds = get_credentials()
    except Exception as e:
        print(f"❌ 認証エラー: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        budget = get_budget_status(creds)
    except Exception as e:
        print(f"❌ Budget API エラー: {e}", file=sys.stderr)
        sys.exit(1)

    total_spent   = parse_money(budget.get("currentSpend", {}))
    remaining     = max(TOTAL_CREDITS - total_spent, 0.0)
    remaining_pct = (remaining / TOTAL_CREDITS) * 100 if TOTAL_CREDITS > 0 else 0.0

    # 前回の累計と比較して「今日の使用額」を算出
    state       = load_state()
    last_spent  = state["last_spent"]
    last_date   = state["last_date"]

    # 月が変わると累計がリセットされるので、前回より小さければ 0 スタート扱い
    today_spent = max(total_spent - last_spent, 0.0)

    print(f"  前回記録日         : {last_date or '(初回)'}")
    print(f"  前回の累計使用額   : ${last_spent:.4f}")
    print(f"  現在の累計使用額   : ${total_spent:.4f}")
    print(f"  今日の使用額       : ${today_spent:.4f}")
    print(f"  残高               : ${remaining:.4f} ({remaining_pct:.1f}%)")

    send_daily_report(today_spent, total_spent, remaining, remaining_pct)

    save_state(total_spent, today_str)
    print("💾 state.json を更新しました")
    print("=== 完了 ===")


if __name__ == "__main__":
    main()
