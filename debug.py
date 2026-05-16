#!/usr/bin/env python3
import os, json, requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession, Request as GoogleRequest

GCP_SA_JSON       = os.environ["GCP_SA_JSON"]
BILLING_ACCOUNT_ID = os.environ["BILLING_ACCOUNT_ID"]
BUDGET_ID         = os.environ["BUDGET_ID"]

print("=== デバッグ開始 ===\n")

# 1. 認証テスト
print("① 認証テスト...")
sa_info = json.loads(GCP_SA_JSON)
print(f"   サービスアカウント: {sa_info.get('client_email', '不明')}")
print(f"   プロジェクトID    : {sa_info.get('project_id', '不明')}")

creds = service_account.Credentials.from_service_account_info(
    sa_info,
    scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"]
)
creds.refresh(GoogleRequest())
print("   → 認証OK\n")

session = AuthorizedSession(creds)

# 2. Billing Account アクセステスト
print("② 請求先アカウントへのアクセステスト...")
print(f"   BILLING_ACCOUNT_ID: {BILLING_ACCOUNT_ID}")
r = session.get(f"https://cloudbilling.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT_ID}")
print(f"   ステータス: {r.status_code}")
if r.status_code == 200:
    print("   → OK")
elif r.status_code == 403:
    print("   → 403: 請求先アカウントへの権限がない")
elif r.status_code == 404:
    print("   → 404: BILLING_ACCOUNT_IDが間違っている可能性")
else:
    print(f"   → {r.text[:200]}")

print()

# 3. Budget API テスト
print("③ バジェットAPIテスト...")
print(f"   BUDGET_ID: {BUDGET_ID}")
url = f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT_ID}/budgets/{BUDGET_ID}"
print(f"   URL: {url}")
r2 = session.get(url)
print(f"   ステータス: {r2.status_code}")
if r2.status_code == 200:
    print("   → OK")
    print(f"   レスポンス: {r2.text[:300]}")
else:
    print(f"   → エラー: {r2.text[:300]}")

# 4. バジェット一覧テスト
print("\n④ バジェット一覧テスト（BUDGET_IDなしで全取得）...")
url2 = f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT_ID}/budgets"
r3 = session.get(url2)
print(f"   ステータス: {r3.status_code}")
if r3.status_code == 200:
    data = r3.json()
    budgets = data.get("budgets", [])
    print(f"   バジェット数: {len(budgets)}")
    for b in budgets:
        name = b.get("name", "")
        display = b.get("displayName", "")
        print(f"   - {display}: {name}")
        print(f"     → BUDGET_IDに使う値: {name.split('/')[-1]}")
else:
    print(f"   → エラー: {r3.text[:300]}")

print("\n=== デバッグ完了 ===")
