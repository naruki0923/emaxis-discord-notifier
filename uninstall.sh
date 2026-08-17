#!/bin/zsh
set -euo pipefail

readonly LABEL="jp.emaxis.discord-notifier"
readonly PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
readonly RUNTIME_DIR="$HOME/Library/Application Support/emaxis-discord-notifier"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST_PATH"
rm -rf "$RUNTIME_DIR"
/usr/bin/security delete-generic-password \
  -s "emaxis-discord-notifier" \
  -a "discord-webhook-url" >/dev/null 2>&1 || true

echo "自動通知の登録とキーチェーンのWebhook URLを削除しました。"
