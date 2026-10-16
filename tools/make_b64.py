"""Firebase service-account JSON  ->  base64 (Render env FIREBASE_CREDENTIALS_B64 ke liye).
Use:  python tools/make_b64.py serviceAccount.json
"""
import sys, json, base64
if len(sys.argv) < 2:
    sys.exit("Use: python tools/make_b64.py serviceAccount.json")
raw = open(sys.argv[1], "rb").read()
info = json.loads(raw)
assert info.get("type") == "service_account" and info.get("private_key"), "Yeh service account JSON nahi lag raha"
print(base64.b64encode(json.dumps(info, separators=(",", ":")).encode()).decode())
