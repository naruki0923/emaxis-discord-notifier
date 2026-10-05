#!/usr/bin/env python3
"""保有中の eMAXIS Slim の評価額と前営業日比をDiscordへ通知する。"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


JST = timezone(timedelta(hours=9))
KEYCHAIN_SERVICE = "emaxis-discord-notifier"
KEYCHAIN_ACCOUNT = "discord-webhook-url"
USER_AGENT = "emaxis-discord-notifier/1.0"
PORTFOLIO_PATH = Path(__file__).with_name("portfolio.json")


@dataclass(frozen=True)
class Fund:
    key: str
    label: str
    name: str
    mufg_code: str
    isin: str
    association_code: str

    @property
    def api_url(self) -> str:
        return f"https://www.am.mufg.jp/mukamapi/fund_details/?fund_cd={self.mufg_code}"

    # 三菱UFJ側は国外・データセンターのIPを拒否するため、投資信託協会の公開データを代替に使う。
    @property
    def toushin_csv_url(self) -> str:
        return (
            "https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download"
            f"?isinCd={self.isin}&associFundCd={self.association_code}"
        )


FUNDS = (
    Fund(
        key="sp500",
        label="S＆P500",
        name="eMAXIS Slim 米国株式（S&P500）",
        mufg_code="253266",
        isin="JP90C000GKC6",
        association_code="03311187",
    ),
    Fund(
        key="allcountry",
        label="オルカン",
        name="eMAXIS Slim 全世界株式（オール・カントリー）",
        mufg_code="253425",
        isin="JP90C000H1T1",
        association_code="0331418A",
    ),
)
FUND_ALIASES = {
    "sp500": "sp500",
    "s&p500": "sp500",
    "allcountry": "allcountry",
    "orukan": "allcountry",
    "オルカン": "allcountry",
}


class NotifierError(RuntimeError):
    """通知処理でユーザーに提示できるエラー。"""


@dataclass(frozen=True)
class FundPrice:
    date: str
    price: int
    change: int
    source: str = "三菱UFJアセットマネジメント公式サイト"

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


@dataclass(frozen=True)
class Holding:
    """update.sh で登録した保有内容と、毎月の積立設定。"""

    units: int
    acquisition_amount: int
    as_of: date | None = None
    monthly_amount: int = 0
    monthly_day: int = 0
    # 積立以外の単発の買付（注文日, 金額）。SBI証券の表示に反映される前の分を推定するために使う。
    orders: tuple[tuple[date, int], ...] = ()


@dataclass(frozen=True)
class Purchase:
    date: date
    amount: int
    units: int


@dataclass(frozen=True)
class FundReport:
    fund: Fund
    price: FundPrice
    portfolio: Portfolio | None = None
    purchases: tuple[Purchase, ...] = field(default_factory=tuple)

    def daily_change(self) -> int:
        """保有分の前営業日比。基準日に約定した口数は前営業日に持っていないので除く。"""
        if self.portfolio is None:
            return 0
        price_date = parse_japanese_date(self.price.date)
        new_units = sum(p.units for p in self.purchases if p.date >= price_date)
        held = Portfolio(self.portfolio.units - new_units, self.portfolio.acquisition_amount)
        return held.daily_change(self.price)


def request_bytes(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    accept: str = "application/json",
) -> bytes:
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 拒否や不在は繰り返しても結果が変わらないため、すぐ次の取得先へ移る。
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(2**attempt)

    raise NotifierError(f"通信に失敗しました: {last_error}")


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    body = request_bytes(url, method=method, payload=payload)
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotifierError(f"応答をJSONとして解釈できませんでした: {exc}") from exc


def parse_japanese_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y年%m月%d日").date()
    except ValueError as exc:
        raise NotifierError(f"日付を解釈できませんでした: {text}") from exc


def parse_toushin_history(text: str) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for line in text.splitlines()[1:]:
        columns = line.split(",")
        if len(columns) < 2:
            continue
        date_raw = columns[0].strip()
        price_raw = columns[1].strip().replace(",", "")
        if not date_raw or not price_raw.isdigit():
            continue
        rows.append((date_raw, int(price_raw)))
    return rows


def parse_toushin_csv(text: str) -> FundPrice:
    rows = parse_toushin_history(text)
    if len(rows) < 2:
        raise NotifierError("投資信託協会のデータに基準価額が2営業日分ありません。")

    date_str, price = rows[-1]
    _, previous_price = rows[-2]
    return FundPrice(
        date=date_str,
        price=price,
        change=price - previous_price,
        source="投資信託協会 投信総合検索ライブラリー",
    )


def fetch_toushin_text(fund: Fund) -> str:
    body = request_bytes(fund.toushin_csv_url, accept="text/csv,*/*")
    try:
        return body.decode("cp932")
    except UnicodeDecodeError as exc:
        raise NotifierError("投資信託協会のCSVを解読できませんでした。") from exc


def fetch_price_history(fund: Fund) -> list[tuple[date, int]]:
    return [(parse_japanese_date(d), p) for d, p in parse_toushin_history(fetch_toushin_text(fund))]


def fetch_fund_price(fund: Fund) -> FundPrice:
    try:
        return fetch_fund_price_from_mufg(fund)
    except NotifierError as mufg_error:
        try:
            return parse_toushin_csv(fetch_toushin_text(fund))
        except NotifierError as toushin_error:
            raise NotifierError(
                f"基準価額を取得できませんでした（公式サイト: {mufg_error} / 投資信託協会: {toushin_error}）"
            ) from toushin_error


def fetch_fund_price_from_mufg(fund: Fund) -> FundPrice:
    response = request_json(fund.api_url)
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
    date_str = f"{date_raw[:4]}年{date_raw[4:6]}月{date_raw[6:]}日"
    return FundPrice(date=date_str, price=price, change=change)


def estimate_purchases(holding: Holding, history: list[tuple[date, int]]) -> list[Purchase]:
    """as_of より後に約定した積立・単発の買付を、公開されている基準価額から推定する。

    注文日（休日なら翌営業日）の翌営業日の基準価額で約定する前提。
    営業日は基準価額が公表された日で判定する。約定日の基準価額がまだ無い回は数えない。
    """
    if holding.as_of is None or not history:
        return []

    history = sorted(history)
    dates = [d for d, _ in history]
    orders = list(holding.orders)
    if holding.monthly_amount > 0:
        year, month = holding.as_of.year, holding.as_of.month
        while (year, month) <= (dates[-1].year, dates[-1].month):
            day = min(holding.monthly_day, calendar.monthrange(year, month)[1])
            orders.append((date(year, month, day), holding.monthly_amount))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    purchases: list[Purchase] = []
    for order_date, amount in orders:
        order_index = next((i for i, d in enumerate(dates) if d >= order_date), None)
        if order_index is None or order_index + 1 >= len(dates):
            continue
        trade_date, price = history[order_index + 1]
        if trade_date > holding.as_of:
            purchases.append(Purchase(date=trade_date, amount=amount, units=amount * 10_000 // price))
    return sorted(purchases, key=lambda p: p.date)


def resolve_fund_key(name: str) -> str:
    key = FUND_ALIASES.get(name.strip().lower())
    if key is None:
        choices = " / ".join(FUND_ALIASES)
        raise NotifierError(f"銘柄「{name}」は対象外です。次のいずれかを指定してください: {choices}")
    return key


def parse_holding(data: Any) -> Holding:
    try:
        units = int(data["units"])
        acquisition_amount = int(data["acquisition_amount"])
        as_of_raw = data.get("as_of")
        as_of = date.fromisoformat(as_of_raw) if as_of_raw else None
        monthly = data.get("monthly") or {}
        monthly_amount = int(monthly.get("amount", 0))
        monthly_day = int(monthly.get("day", 0))
        orders = tuple(
            (date.fromisoformat(order["date"]), int(order["amount"])) for order in data.get("orders") or []
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise NotifierError("保有情報の形式が正しくありません。") from exc
    if units < 0 or acquisition_amount < 0 or monthly_amount < 0 or any(a <= 0 for _, a in orders):
        raise NotifierError("保有口数・取得金額・積立金額には0以上、買付金額には1以上の数を指定してください。")
    if monthly_amount > 0 and not 1 <= monthly_day <= 31:
        raise NotifierError("積立日には1〜31を指定してください。")
    return Holding(units, acquisition_amount, as_of, monthly_amount, monthly_day, orders)


def parse_holdings(data: Any) -> dict[str, Holding]:
    if not isinstance(data, dict):
        raise NotifierError("保有情報の形式が正しくありません。")
    # 1銘柄だけだった頃の形式（units と acquisition_amount だけ）は S&P500 として読む。
    if "units" in data:
        return {"sp500": parse_holding(data)}
    return {resolve_fund_key(key): parse_holding(value) for key, value in data.items()}


def load_holdings() -> dict[str, Holding]:
    env_json = os.environ.get("PORTFOLIO_JSON", "").strip()
    if env_json:
        try:
            return parse_holdings(json.loads(env_json))
        except json.JSONDecodeError as exc:
            raise NotifierError("PORTFOLIO_JSONをJSONとして解釈できませんでした。") from exc

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
        return parse_holdings({"units": units, "acquisition_amount": acquisition_amount})

    if not PORTFOLIO_PATH.exists():
        return {}
    try:
        data = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NotifierError("portfolio.jsonの形式が正しくありません。") from exc
    return parse_holdings(data)


def build_report(fund: Fund, holding: Holding | None) -> FundReport:
    price = fetch_fund_price(fund)
    if holding is None:
        return FundReport(fund, price)

    purchases: list[Purchase] = []
    if (holding.monthly_amount > 0 or holding.orders) and holding.as_of is not None:
        purchases = estimate_purchases(holding, fetch_price_history(fund))
    portfolio = Portfolio(
        units=holding.units + sum(p.units for p in purchases),
        acquisition_amount=holding.acquisition_amount + sum(p.amount for p in purchases),
    )
    return FundReport(fund, price, portfolio, tuple(purchases))


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


def signed(value: int) -> str:
    return f"+{value:,}" if value > 0 else f"{value:,}"


def build_message(reports: list[FundReport], errors: list[tuple[Fund, str]] | None = None) -> str:
    errors = errors or []
    failed = {fund.key for fund, _ in errors}
    by_key = {report.fund.key: report for report in reports}
    held = [r for r in reports if r.portfolio is not None]
    total = sum(r.portfolio.valuation(r.price) for r in held)
    invested = sum(r.portfolio.acquisition_amount for r in held)

    assets = [f"全体資産：{total:,}円"]
    changes: list[str] = []
    for fund in FUNDS:
        report = by_key.get(fund.key)
        if fund.key in failed:
            assets.append(f"{fund.label}：取得できませんでした")
            changes.append(f"{fund.label}：取得できませんでした")
        elif report is not None and report.portfolio is not None:
            assets.append(f"{fund.label}：{report.portfolio.valuation(report.price):,}円")
            changes.append(f"{fund.label}：{signed(report.daily_change())}円")
        else:
            assets.append(f"{fund.label}：保有情報が未設定です")
            changes.append(f"{fund.label}：保有情報が未設定です")

    return "\n".join(
        [
            "・資産",
            *assets,
            "",
            "・前営業日比",
            *changes,
            "",
            "・損益",
            f"投資金額：{invested:,}円",
            f"資産：{total:,}円",
        ]
    )


def send_to_discord(webhook_url: str, content: str) -> None:
    if not webhook_url.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        raise NotifierError("Discord Webhook URLの形式が正しくありません。")
    request_json(
        webhook_url,
        method="POST",
        payload={
            "username": "投資信託 基準価額通知",
            "allowed_mentions": {"parse": []},
            "content": content,
        },
    )


def print_details(reports: list[FundReport], errors: list[tuple[Fund, str]]) -> None:
    """--dry-run のときだけ、通知文の根拠を表示する。"""
    for report in reports:
        price = report.price
        print(f"[{report.fund.name}]")
        print(f"  基準日 {price.date} / 基準価額 {price.price:,}円 / 前営業日比 {signed(price.change)}円")
        print(f"  取得先: {price.source}")
        if report.portfolio is not None:
            portfolio = report.portfolio
            print(
                f"  保有 {portfolio.units:,}口 / 取得金額 {portfolio.acquisition_amount:,}円"
                f" / 評価損益 {signed(portfolio.profit(price))}円"
            )
        for purchase in report.purchases:
            print(f"  買付（推定）: {purchase.date} 約定 {purchase.amount:,}円 → {purchase.units:,}口")
    for fund, message in errors:
        print(f"[{fund.name}] エラー: {message}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Discordへ送らず、取得結果だけ表示する")
    args = parser.parse_args()

    try:
        holdings = load_holdings()
        reports: list[FundReport] = []
        errors: list[tuple[Fund, str]] = []
        for fund in FUNDS:
            try:
                reports.append(build_report(fund, holdings.get(fund.key)))
            except NotifierError as exc:
                errors.append((fund, str(exc)))
        for fund, message in errors:
            print(f"エラー: {fund.name}: {message}", file=sys.stderr)
        if not reports:
            return 1

        message = build_message(reports, errors)
        if args.dry_run:
            print_details(reports, errors)
            print(message)
        else:
            send_to_discord(get_webhook_url(), message)
            print("Discordへ通知しました:")
            print(message)
        return 1 if errors else 0
    except NotifierError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
