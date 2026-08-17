#!/bin/zsh
set -euo pipefail

readonly PROJECT_DIR="${0:A:h}"
readonly PORTFOLIO_PATH="$PROJECT_DIR/portfolio.json"

usage() {
  cat <<'EOF'
使い方: ./update.sh <保有口数> <取得金額> [--notify]

SBI証券の最新表示に合わせて、保有口数と取得金額を更新します。
GitHub Secretsとローカルのportfolio.jsonの両方に反映します。

例: ./update.sh 2,402 12,000
    ./update.sh 2402 12000 --notify   # 更新後すぐにDiscordへ通知する
EOF
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
  exit 1
fi

UNITS="${1//[,口]/}"
AMOUNT="${2//[,円]/}"
NOTIFY="no"

if [[ $# -eq 3 ]]; then
  if [[ "$3" == "--notify" ]]; then
    NOTIFY="yes"
  else
    usage
    exit 1
  fi
fi

for value in "$UNITS" "$AMOUNT"; do
  case "$value" in
    ""|*[!0-9]*)
      echo "エラー: 保有口数と取得金額には0以上の整数を指定してください。" >&2
      exit 1
      ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "エラー: ghコマンドが見つかりません。GitHub CLIをインストールしてください。" >&2
  exit 1
fi

cd "$PROJECT_DIR"
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

gh secret set PORTFOLIO_UNITS --repo "$REPO" --body "$UNITS" >/dev/null
gh secret set PORTFOLIO_ACQUISITION_AMOUNT --repo "$REPO" --body "$AMOUNT" >/dev/null

printf '{\n  "units": %s,\n  "acquisition_amount": %s\n}\n' "$UNITS" "$AMOUNT" > "$PORTFOLIO_PATH"
chmod 600 "$PORTFOLIO_PATH"

echo "更新しました: $REPO"
echo

PORTFOLIO_UNITS="$UNITS" PORTFOLIO_ACQUISITION_AMOUNT="$AMOUNT" python3 notifier.py --dry-run

if [[ "$NOTIFY" == "yes" ]]; then
  echo
  gh workflow run notify.yml --repo "$REPO"
  echo "Discordへの通知を実行しました。結果は gh run list --workflow=notify.yml で確認できます。"
else
  echo
  echo "次回の朝8時の通知から反映されます。"
fi
