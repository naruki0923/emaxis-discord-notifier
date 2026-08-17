#!/bin/zsh
set -euo pipefail

readonly PROJECT_DIR="${0:A:h}"
readonly LABEL="jp.emaxis.discord-notifier"
readonly PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
readonly LOG_DIR="$HOME/Library/Logs/emaxis-discord-notifier"
readonly RUNTIME_DIR="$HOME/Library/Application Support/emaxis-discord-notifier"
readonly SERVICE="emaxis-discord-notifier"
readonly ACCOUNT="discord-webhook-url"

if /usr/bin/security find-generic-password -s "$SERVICE" -a "$ACCOUNT" >/dev/null 2>&1; then
  echo "保存済みのDiscord Webhook URLを再利用します。"
else
  echo "Discordのチャンネル設定 → 連携サービス → ウェブフック でURLをコピーしてください。"
  read -r -s "WEBHOOK_URL?Discord Webhook URL: "
  echo

  case "$WEBHOOK_URL" in
    https://discord.com/api/webhooks/*|https://discordapp.com/api/webhooks/*) ;;
    *)
      echo "エラー: Discord Webhook URLの形式が正しくありません。" >&2
      exit 1
      ;;
  esac

  /usr/bin/security add-generic-password -U -s "$SERVICE" -a "$ACCOUNT" -w "$WEBHOOK_URL" >/dev/null
  unset WEBHOOK_URL
fi

mkdir -p "${PLIST_PATH:h}" "$LOG_DIR" "$RUNTIME_DIR"
cp "$PROJECT_DIR/notifier.py" "$PROJECT_DIR/portfolio.json" "$RUNTIME_DIR/"
chmod 700 "$RUNTIME_DIR/notifier.py"
chmod 600 "$RUNTIME_DIR/portfolio.json"

sed \
  -e "s|__RUNTIME_DIR__|$RUNTIME_DIR|g" \
  -e "s|__LOG_DIR__|$LOG_DIR|g" \
  "$PROJECT_DIR/jp.emaxis.discord-notifier.plist.template" > "$PLIST_PATH"

chmod 600 "$PLIST_PATH"
chmod +x "$PROJECT_DIR/notifier.py"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

echo "テスト通知を送信します..."
if "$RUNTIME_DIR/notifier.py"; then
  echo "設定完了: 毎日8:00（このMacの時刻）にDiscordへ通知します。"
else
  echo "自動実行は登録されましたが、テスト通知に失敗しました。" >&2
  echo "ログ: $LOG_DIR" >&2
  exit 1
fi
