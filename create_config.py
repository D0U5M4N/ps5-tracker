import json, os, sys

token   = os.environ.get("TELEGRAM_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

if not token or not chat_id:
    print("ERROR: Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    sys.exit(1)

config = {
    "telegram_token":      token,
    "telegram_chat_id":    chat_id,
    "alert_threshold_clp": 500000,
    "send_daily_summary":  True,
}

with open("config.json", "w") as f:
    json.dump(config, f, indent=2)

print(f"config.json OK (chat_id: ...{chat_id[-4:]})")
