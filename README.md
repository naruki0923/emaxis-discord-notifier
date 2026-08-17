# eMAXIS Slim 米国株式（S&P500）Discord通知

三菱UFJアセットマネジメント公式サイトから、直近公表の基準価額と前営業日比を取得し、毎朝8時（JST）にDiscordへ通知します。土日・祝日も実行され、その時点で公表済みの直近営業日の値を送ります。

保有口数と取得金額を設定すると、現在の評価額、前日増減、購入後の評価損益も通知します。追加購入または売却をした場合は、この2項目をSBI証券の最新表示に合わせて更新してください。

実行はGitHub Actions上で行うため、Macの電源やスリープ状態に関係なく通知が届きます。

## 設定（初回のみ）

1. Discordで通知先チャンネルを開き、`チャンネルの編集` → `連携サービス` → `ウェブフック` → `新しいウェブフック` を選び、`ウェブフックURLをコピー` します。
2. GitHubのプライベートリポジトリにこのフォルダをpushします。
3. リポジトリに次のSecretsを登録します（`Settings` → `Secrets and variables` → `Actions`、またはghコマンド）。

| Secret名 | 内容 |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | コピーしたDiscord Webhook URL |
| `PORTFOLIO_UNITS` | 保有口数（例: `2202`） |
| `PORTFOLIO_ACQUISITION_AMOUNT` | 取得金額（円、例: `10000`） |

ghコマンドの場合:

```sh
gh secret set DISCORD_WEBHOOK_URL
./update.sh <保有口数> <取得金額>
```

保有情報はリポジトリに含めず、Secretsだけに保存します（`portfolio.json` は `.gitignore` 済み）。

## 保有口数・取得金額の更新

追加購入または売却をしたら、SBI証券の最新表示の値をそのまま渡します。桁区切りのカンマや「口」「円」は付いていても構いません。

```sh
./update.sh 2,402 12,000
```

GitHub Secretsとローカルの `portfolio.json` の両方を更新し、更新後の評価額をその場で表示します。反映は次回の朝8時の通知からです。すぐにDiscordへ送って確認したい場合:

```sh
./update.sh 2,402 12,000 --notify
```

## 手動実行と確認

今すぐ通知を送る場合:

```sh
gh workflow run notify.yml
```

実行結果とログはGitHubの `Actions` タブ、またはターミナルで確認できます。

```sh
gh run list --workflow=notify.yml
```

## 手元での確認

Discordへ送らず取得結果だけを見る場合:

```sh
PORTFOLIO_UNITS=2202 PORTFOLIO_ACQUISITION_AMOUNT=10000 python3 notifier.py --dry-run
```

`portfolio.json` を置いておくと、環境変数がなくてもその値が使われます（環境変数が優先）。

## 停止

`.github/workflows/notify.yml` の `schedule` 行を削除してpushするか、GitHubの `Actions` タブでワークフローを `Disable` します。

## データの取得先

三菱UFJアセットマネジメントの公式APIは国外・データセンターのIPからのアクセスを403で拒否するため、GitHub Actions上では投資信託協会の「投信総合検索ライブラリー」の公開データを使います。手元のMacから実行した場合は公式APIが使われ、失敗したときだけ投資信託協会へ切り替わります。通知の下部に、その回に使った取得先が表示されます。

## 注意

- 通知時刻はJSTの8:00ですが、GitHub Actionsのスケジュール実行は混雑時に数分〜30分程度遅れることがあります。
- 投資信託協会のデータは公式サイトより反映が遅く、基準日が1営業日前になることがあります。通知に基準日を出しているので確認できます。
- 基準価額は1万口あたりで、前営業日比は公式データを基に計算・表示します。
- この通知は情報提供のみを目的とし、投資助言ではありません。

## 付録: Mac上で動かす場合

`setup.sh` / `uninstall.sh` はmacOSのlaunchd（LaunchAgent）で動かすための旧構成です。Webhook URLはキーチェーン、保有情報は `portfolio.json` から読みます。Macが起動している間だけ通知されます。
