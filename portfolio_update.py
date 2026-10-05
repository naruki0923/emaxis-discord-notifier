#!/usr/bin/env python3
"""portfolio.json の1銘柄分を、SBI証券の最新表示に合わせて書き換える（update.sh から呼ぶ）。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from notifier import (
    FUND_ALIASES,
    FUNDS,
    JST,
    PORTFOLIO_PATH,
    NotifierError,
    parse_holding,
    parse_holdings,
    resolve_fund_key,
)


def parse_number(text: str) -> int:
    cleaned = text.replace(",", "").replace("口", "").replace("円", "").strip()
    if not re.fullmatch(r"[0-9]+", cleaned):
        raise NotifierError(f"「{text}」は0以上の整数として読めません。")
    return int(cleaned)


def holding_to_dict(holding) -> dict:
    data = {
        "units": holding.units,
        "acquisition_amount": holding.acquisition_amount,
        "as_of": holding.as_of.isoformat() if holding.as_of else None,
    }
    if holding.monthly_amount > 0:
        data["monthly"] = {"amount": holding.monthly_amount, "day": holding.monthly_day}
        if holding.monthly_since:
            data["monthly"]["since"] = holding.monthly_since.isoformat()
    if holding.orders:
        data["orders"] = [{"date": d.isoformat(), "amount": a} for d, a in holding.orders]
    return data


def write_private(path: Path, text: str) -> None:
    """最初から所有者だけが読める一時ファイルに書いてから置き換え、途中で止まっても壊さない。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=f"銘柄: {' / '.join(FUND_ALIASES)}",
    )
    parser.add_argument("values", nargs="+", metavar="[銘柄] 保有口数 取得金額")
    parser.add_argument("--monthly", help="毎月の積立金額（円）。0で積立なし")
    parser.add_argument("--day", help="毎月の積立日（1〜31）")
    parser.add_argument("--buy", help="単発の買付金額（円）。SBI証券の表示に反映されるまで推定で加算する")
    parser.add_argument(
        "--buy-date",
        help="--buy の申込日（YYYY-MM-DD）。省略時は今日。15:30以降の注文は翌営業日の申込になる",
    )
    parser.add_argument("--clear-buys", action="store_true", help="この銘柄に登録済みの単発の買付をすべて取り消す")
    args = parser.parse_args(argv)

    try:
        # 銘柄を省略した旧来の書き方（./update.sh 口数 金額）は S&P500 として扱う。
        if len(args.values) == 2:
            fund_name, units_raw, amount_raw = "sp500", *args.values
        elif len(args.values) == 3:
            fund_name, units_raw, amount_raw = args.values
        else:
            parser.error("銘柄・保有口数・取得金額を指定してください。")
        key = resolve_fund_key(fund_name)

        current: dict = {}
        if PORTFOLIO_PATH.exists():
            loaded = parse_holdings(json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8")))
            current = {k: holding_to_dict(v) for k, v in loaded.items()}

        previous_monthly = (current.get(key) or {}).get("monthly") or {}
        monthly_amount = (
            parse_number(args.monthly) if args.monthly is not None else previous_monthly.get("amount", 0)
        )
        monthly_day = parse_number(args.day) if args.day is not None else previous_monthly.get("day", 0)

        today = today or datetime.now(JST).date()
        # 前回までの単発の買付は、約定がSBI証券の表示に入ったかを as_of で判定するため残しておく。
        # 約定から十分たったものは表示に入っているはずなので消す。
        orders = [
            order
            for order in (current.get(key) or {}).get("orders") or []
            if not args.clear_buys and order["date"] >= (today - timedelta(days=14)).isoformat()
        ]
        if args.buy is not None:
            try:
                buy_date = date.fromisoformat(args.buy_date) if args.buy_date else today
            except ValueError as exc:
                raise NotifierError("--buy-date は YYYY-MM-DD の形で指定してください。") from exc
            order = {"date": buy_date.isoformat(), "amount": parse_number(args.buy)}
            # 打ち直しで同じ買付が二重に入らないよう、同じ申込日・金額のものは1件にする。
            if order in orders:
                print(f"同じ買付（{order['date']} 申込 {order['amount']:,}円）は登録済みのため追加しません。")
            else:
                orders.append(order)
        elif args.buy_date:
            raise NotifierError("--buy-date は --buy と一緒に指定してください。")

        entry = {
            "units": parse_number(units_raw),
            "acquisition_amount": parse_number(amount_raw),
            "as_of": today.isoformat(),
        }
        if monthly_amount > 0:
            entry["monthly"] = {"amount": monthly_amount, "day": monthly_day}
            # 積立の内容が変わらなければ設定日を引き継ぎ、新しく設定・変更したら今日を設定日にする。
            unchanged = (previous_monthly.get("amount"), previous_monthly.get("day")) == (monthly_amount, monthly_day)
            since = previous_monthly.get("since") if unchanged else today.isoformat()
            if since:
                entry["monthly"]["since"] = since
        if orders:
            entry["orders"] = orders
        parse_holding(entry)  # 書き込む前に形式を確かめる
        current[key] = entry
    except NotifierError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"エラー: portfolio.jsonを読めませんでした: {exc}", file=sys.stderr)
        return 1

    ordered = {fund.key: current[fund.key] for fund in FUNDS if fund.key in current}
    write_private(PORTFOLIO_PATH, json.dumps(ordered, ensure_ascii=False, indent=2) + "\n")

    fund = next(f for f in FUNDS if f.key == key)
    monthly = entry.get("monthly")
    plan = f"毎月{monthly['day']}日に{monthly['amount']:,}円" if monthly else "なし"
    print(
        f"{fund.name}: {entry['units']:,}口 / 取得金額 {entry['acquisition_amount']:,}円"
        f"（{entry['as_of']}時点）/ 積立: {plan}"
    )
    for order in entry.get("orders", []):
        print(f"  単発の買付: {order['date']} 申込 {order['amount']:,}円（約定がこの時点より後なら推定で加算）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
