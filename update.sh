#!/bin/zsh
set -euo pipefail

readonly PROJECT_DIR="${0:A:h}"
readonly PORTFOLIO_PATH="$PROJECT_DIR/portfolio.json"

usage() {
  cat <<'EOF'
使い方: ./update.sh <銘柄> <保有口数> <取得金額> [--monthly 積立金額 --day 積立日] [--notify]

SBI証券の最新表示に合わせて、1銘柄分の保有口数と取得金額を更新します。
GitHub Secrets（PORTFOLIO_JSON）とローカルのportfolio.jsonの両方に反映します。
銘柄: sp500 / オルカン（allcountry）

例: ./update.sh sp500 2,402 12,000
    ./update.sh オルカン 1,320 5,000 --monthly 5,000 --day 10
    ./update.sh sp500 2,402 12,000 --notify   # 更新後すぐにDiscordへ通知する
EOF
}

NOTIFY="no"
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --notify) NOTIFY="yes" ;;
    -h|--help) usage; exit 0 ;;
    *) ARGS+=("$arg") ;;
  esac
done

if [[ ${#ARGS[@]} -lt 2 ]]; then
  usage
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "エラー: ghコマンドが見つかりません。GitHub CLIをインストールしてください。" >&2
  exit 1
fi

cd "$PROJECT_DIR"
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

python3 portfolio_update.py "${ARGS[@]}"
gh secret set PORTFOLIO_JSON --repo "$REPO" < "$PORTFOLIO_PATH" >/dev/null

echo "更新しました: $REPO"
echo

python3 notifier.py --dry-run

if [[ "$NOTIFY" == "yes" ]]; then
  echo
  gh workflow run notify.yml --repo "$REPO"
  echo "Discordへの通知を実行しました。結果は gh run list --workflow=notify.yml で確認できます。"
else
  echo
  echo "次回の朝8時の通知から反映されます。"
fi
