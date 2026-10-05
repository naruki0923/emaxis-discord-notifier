#!/usr/bin/env python3
"""portfolio.json の1銘柄分を、SBI証券の最新表示に合わせて書き換える（update.sh から呼ぶ）。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

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
    if not cleaned.isdigit():
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
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=f"銘柄: {' / '.join(FUND_ALIASES)}",
    )
    parser.add_argument("values", nargs="+", metavar="[銘柄] 保有口数 取得金額")
    parser.add_argument("--monthly", help="毎月の積立金額（円）。0で積立なし")
    parser.add_argument("--day", help="毎月の積立日（1〜31）")
    args = parser.parse_args()

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

        entry = {
            "units": parse_number(units_raw),
            "acquisition_amount": parse_number(amount_raw),
            "as_of": datetime.now(JST).date().isoformat(),
        }
        if monthly_amount > 0:
            entry["monthly"] = {"amount": monthly_amount, "day": monthly_day}
        parse_holding(entry)  # 書き込む前に形式を確かめる
        current[key] = entry
    except NotifierError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"エラー: portfolio.jsonを読めませんでした: {exc}", file=sys.stderr)
        return 1

    ordered = {fund.key: current[fund.key] for fund in FUNDS if fund.key in current}
    PORTFOLIO_PATH.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PORTFOLIO_PATH.chmod(0o600)

    fund = next(f for f in FUNDS if f.key == key)
    monthly = entry.get("monthly")
    plan = f"毎月{monthly['day']}日に{monthly['amount']:,}円" if monthly else "なし"
    print(
        f"{fund.name}: {entry['units']:,}口 / 取得金額 {entry['acquisition_amount']:,}円"
        f"（{entry['as_of']}時点）/ 積立: {plan}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
