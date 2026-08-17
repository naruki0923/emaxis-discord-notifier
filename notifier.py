#!/usr/bin/env python3
"""eMAXIS Slim 米国株式（S&P500）の基準価額をDiscordへ通知する。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


FUND_CODE = "253266"
FUND_NAME = "eMAXIS Slim 米国株式（S&P500）"
FUND_URL = f"https://emaxis.am.mufg.jp/fund/{FUND_CODE}.html"
API_URL = f"https://www.am.mufg.jp/mukamapi/fund_details/?fund_cd={FUND_CODE}"
KEYCHAIN_SERVICE = "emaxis-discord-notifier"
KEYCHAIN_ACCOUNT = "discord-webhook-url"
USER_AGENT = "emaxis-discord-notifier/1.0"
PORTFOLIO_PATH = Path(__file__).with_name("portfolio.json")


class NotifierError(RuntimeError):
    """通知処理でユーザーに提示できるエラー。"""


@dataclass(frozen=True)
class FundPrice:
    date: str
    price: int
    change: int

    @property
    def previous_price(self) -> int:
        return self.price - self.change

    @property
    def change_percent(self) -> Decimal:
        if self.previous_price == 0:
            raise NotifierError("前営業日の基準価額が0のため、騰落率を計算できません。")
        value = Decimal(self.change) * Decimal(100) / Decimal(self.previous_price)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Portfolio:
    units: int
    acquisition_amount: int

    def valuation(self, price: FundPrice) -> int:
        value = Decimal(price.price) * Decimal(self.units) / Decimal(10_000)
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def daily_change(self, price: FundPrice) -> int:
        value = Decimal(price.change) * Decimal(self.units) / Decimal(10_000)
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def profit(self, price: FundPrice) -> int:
        return self.valuation(price) - self.acquisition_amount

    def profit_percent(self, price: FundPrice) -> Decimal:
        if self.acquisition_amount == 0:
            raise NotifierError("取得金額が0のため、評価損益率を計算できません。")
        value = Decimal(self.profit(price)) * Decimal(100) / Decimal(self.acquisition_amount)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)

    raise NotifierError(f"通信に3回失敗しました: {last_error}")


def fetch_fund_price() -> FundPrice:
    response = request_json(API_URL)
    if not isinstance(response, dict) or response.get("result", {}).get("status") != 200:
        raise NotifierError("公式APIから正常な応答を取得できませんでした。")

    data = response.get("datasets")
    if not isinstance(data, dict):
        raise NotifierError("公式APIの応答に基準価額データがありません。")

    try:
        date_raw = str(data["cfm_base_date"])
        price = int(data["cfm_base_price"])
        change = int(data["cfm_price_changes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NotifierError("公式APIのデータ形式が想定と異なります。") from exc

    if len(date_raw) != 8 or not date_raw.isdigit():
        raise NotifierError("公式APIの基準日が想定と異なります。")
    date = f"{date_raw[:4]}年{date_raw[4:6]}月{date_raw[6:]}日"
    return FundPrice(date=date, price=price, change=change)


def load_portfolio() -> Portfolio | None:
    env_units = os.environ.get("PORTFOLIO_UNITS", "").strip()
    env_amount = os.environ.get("PORTFOLIO_ACQUISITION_AMOUNT", "").strip()
    if env_units or env_amount:
        try:
            units = int(env_units)
            acquisition_amount = int(env_amount)
        except ValueError as exc:
            raise NotifierError(
                "PORTFOLIO_UNITSとPORTFOLIO_ACQUISITION_AMOUNTには整数を指定してください。"
            ) from exc
        if units < 0 or acquisition_amount < 0:
            raise NotifierError("保有口数と取得金額には0以上の数を指定してください。")
        return Portfolio(units=units, acquisition_amount=acquisition_amount)

    if not PORTFOLIO_PATH.exists():
        return None
    try:
        data = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        units = int(data["units"])
        acquisition_amount = int(data["acquisition_amount"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise NotifierError("portfolio.jsonの形式が正しくありません。") from exc
    if units < 0 or acquisition_amount < 0:
        raise NotifierError("保有口数と取得金額には0以上の数を指定してください。")
    return Portfolio(units=units, acquisition_amount=acquisition_amount)


def get_webhook_url() -> str:
    env_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if env_url:
        return env_url

    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise NotifierError(
            "Discord Webhook URLが未設定です。先に ./setup.sh を実行してください。"
        ) from exc
    return result.stdout.strip()


def direction(price: FundPrice) -> tuple[str, str, int]:
    if price.change > 0:
        return "📈 値上がり", "+", 0x2ECC71
    if price.change < 0:
        return "📉 値下がり", "", 0xE74C3C
    return "➡️ 変わらず", "", 0x95A5A6


def signed(value: int) -> str:
    return f"+{value:,}" if value > 0 else f"{value:,}"


def signed_decimal(value: Decimal) -> str:
    return f"+{value}" if value > 0 else f"{value}"


def build_discord_payload(price: FundPrice, portfolio: Portfolio | None = None) -> dict[str, Any]:
    label, sign, color = direction(price)
    percent = price.change_percent
    percent_sign = "+" if percent > 0 else ""
    fields: list[dict[str, Any]] = [
        {
            "name": "基準価額（1万口あたり）",
            "value": f"**{price.price:,}円**",
            "inline": True,
        },
        {
            "name": "前営業日比",
            "value": f"**{sign}{price.change:,}円（{percent_sign}{percent}%）**",
            "inline": True,
        },
    ]
    if portfolio is not None:
        valuation = portfolio.valuation(price)
        profit = portfolio.profit(price)
        profit_percent = portfolio.profit_percent(price)
        fields.extend(
            [
                {
                    "name": f"あなたの評価額（{portfolio.units:,}口）",
                    "value": f"**{valuation:,}円**",
                    "inline": True,
                },
                {
                    "name": "あなたの前日増減",
                    "value": f"**{signed(portfolio.daily_change(price))}円**",
                    "inline": True,
                },
                {
                    "name": "購入後の評価損益",
                    "value": f"**{signed(profit)}円（{signed_decimal(profit_percent)}%）**",
                    "inline": False,
                },
            ]
        )
    fields.append({"name": "基準日", "value": price.date, "inline": False})
    return {
        "username": "投資信託 基準価額通知",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": FUND_NAME,
                "url": FUND_URL,
                "description": label,
                "color": color,
                "fields": fields,
                "footer": {
                    "text": "出所: 三菱UFJアセットマネジメント公式サイト（過去の実績であり、将来の成果を保証しません）"
                },
            }
        ],
    }


def send_to_discord(webhook_url: str, payload: dict[str, Any]) -> None:
    if not webhook_url.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        raise NotifierError("Discord Webhook URLの形式が正しくありません。")
    request_json(webhook_url, method="POST", payload=payload)


def print_preview(price: FundPrice, portfolio: Portfolio | None = None) -> None:
    label, sign, _ = direction(price)
    percent = price.change_percent
    percent_sign = "+" if percent > 0 else ""
    print(FUND_NAME)
    print(f"{label} / {price.date}")
    print(f"基準価額: {price.price:,}円")
    print(f"前営業日比: {sign}{price.change:,}円（{percent_sign}{percent}%）")
    if portfolio is not None:
        print(f"あなたの評価額: {portfolio.valuation(price):,}円（{portfolio.units:,}口）")
        print(f"あなたの前日増減: {signed(portfolio.daily_change(price))}円")
        print(
            "購入後の評価損益: "
            f"{signed(portfolio.profit(price))}円（{signed_decimal(portfolio.profit_percent(price))}%）"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Discordへ送らず、取得結果だけ表示する")
    args = parser.parse_args()

    try:
        price = fetch_fund_price()
        portfolio = load_portfolio()
        if args.dry_run:
            print_preview(price, portfolio)
            return 0
        send_to_discord(get_webhook_url(), build_discord_payload(price, portfolio))
        print(f"Discordへ通知しました: {price.date} / {price.price:,}円")
        return 0
    except NotifierError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
