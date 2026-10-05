import json
import os
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

import notifier
from notifier import (
    FUNDS,
    FundPrice,
    FundReport,
    Holding,
    NotifierError,
    Portfolio,
    build_message,
    estimate_purchases,
    load_holdings,
    parse_holdings,
    parse_toushin_csv,
)

SP500, ALLCOUNTRY = FUNDS


class FundPriceTest(unittest.TestCase):
    def test_positive_change_percent_uses_previous_price(self):
        price = FundPrice(date="2026年08月14日", price=45_727, change=303)
        self.assertEqual(price.previous_price, 45_424)
        self.assertEqual(price.change_percent, Decimal("0.67"))

    def test_portfolio_matches_sbi_screenshot(self):
        price = FundPrice(date="2026年08月14日", price=45_727, change=303)
        portfolio = Portfolio(units=2_202, acquisition_amount=10_000)
        self.assertEqual(portfolio.valuation(price), 10_069)
        self.assertEqual(portfolio.daily_change(price), 67)
        self.assertEqual(portfolio.profit(price), 69)
        self.assertEqual(portfolio.profit_percent(price), Decimal("0.69"))


class FundDefinitionTest(unittest.TestCase):
    def test_each_fund_has_its_own_codes(self):
        self.assertEqual(SP500.isin, "JP90C000GKC6")
        self.assertEqual(SP500.association_code, "03311187")
        self.assertEqual(ALLCOUNTRY.isin, "JP90C000H1T1")
        self.assertEqual(ALLCOUNTRY.association_code, "0331418A")
        self.assertIn("fund_cd=253425", ALLCOUNTRY.api_url)


class MessageTest(unittest.TestCase):
    def test_message_format(self):
        reports = [
            FundReport(
                SP500,
                FundPrice(date="2026年10月02日", price=44_658, change=144),
                Portfolio(units=2_402, acquisition_amount=12_000),
            ),
            FundReport(
                ALLCOUNTRY,
                FundPrice(date="2026年10月02日", price=37_827, change=-50),
                Portfolio(units=1_322, acquisition_amount=5_000),
            ),
        ]
        self.assertEqual(
            build_message(reports),
            "・資産\n"
            "全体資産：15,728円\n"
            "S＆P500：10,727円\n"
            "オルカン：5,001円\n"
            "\n"
            "・前営業日比\n"
            "S＆P500：+35円\n"
            "オルカン：-7円\n"
            "\n"
            "・損益\n"
            "投資金額：17,000円\n"
            "資産：15,728円",
        )

    def test_missing_holding_and_error(self):
        reports = [
            FundReport(
                SP500,
                FundPrice(date="2026年10月02日", price=10_000, change=0),
                Portfolio(units=10_000, acquisition_amount=10_000),
            ),
        ]
        message = build_message(reports, [(ALLCOUNTRY, "通信に失敗しました")])
        self.assertIn("S＆P500：0円", message)
        self.assertIn("オルカン：取得できませんでした", message)

        message = build_message(reports[:1] + [FundReport(ALLCOUNTRY, reports[0].price)])
        self.assertIn("オルカン：保有情報が未設定です", message)
        self.assertIn("投資金額：10,000円", message)


class EstimatePurchasesTest(unittest.TestCase):
    # 2026年10月: 10日(土)・11日(日)・12日(月・祝)は基準価額なし
    HISTORY = [
        (date(2026, 10, 8), 37_000),
        (date(2026, 10, 9), 37_100),
        (date(2026, 10, 13), 37_200),
        (date(2026, 10, 14), 37_300),
        (date(2026, 11, 9), 38_000),
        (date(2026, 11, 10), 38_100),
        (date(2026, 11, 11), 38_200),
    ]

    def holding(self, as_of):
        return Holding(units=1_000, acquisition_amount=5_000, as_of=as_of, monthly_amount=5_000, monthly_day=10)

    def test_orders_next_business_day_and_trades_the_day_after(self):
        purchases = estimate_purchases(self.holding(date(2026, 10, 5)), self.HISTORY)
        self.assertEqual([p.date for p in purchases], [date(2026, 10, 14), date(2026, 11, 11)])
        self.assertEqual(purchases[0].units, 5_000 * 10_000 // 37_300)
        self.assertEqual(purchases[1].units, 5_000 * 10_000 // 38_200)

    def test_skips_purchases_already_in_sbi_values(self):
        purchases = estimate_purchases(self.holding(date(2026, 10, 14)), self.HISTORY)
        self.assertEqual([p.date for p in purchases], [date(2026, 11, 11)])

    def test_waits_until_trade_price_is_published(self):
        purchases = estimate_purchases(self.holding(date(2026, 10, 5)), self.HISTORY[:3])
        self.assertEqual(purchases, [])

    def test_one_off_order_is_added_after_trade(self):
        holding = Holding(units=0, acquisition_amount=0, as_of=date(2026, 10, 9), orders=((date(2026, 10, 9), 10_000),))
        purchases = estimate_purchases(holding, self.HISTORY[:3])
        self.assertEqual(purchases, [notifier.Purchase(date(2026, 10, 13), 10_000, 10_000 * 10_000 // 37_200)])
        self.assertEqual(estimate_purchases(holding, self.HISTORY[:2]), [])

    def test_one_off_order_and_monthly_plan_together(self):
        holding = Holding(0, 0, date(2026, 10, 5), 5_000, 10, ((date(2026, 10, 5), 10_000),))
        purchases = estimate_purchases(holding, [(date(2026, 10, 5), 37_000), (date(2026, 10, 6), 37_500)] + self.HISTORY)
        self.assertEqual([(p.date, p.amount) for p in purchases], [
            (date(2026, 10, 6), 10_000), (date(2026, 10, 14), 5_000), (date(2026, 11, 11), 5_000),
        ])

    def test_no_estimate_without_monthly_plan(self):
        holding = Holding(units=1_000, acquisition_amount=5_000, as_of=date(2026, 10, 5))
        self.assertEqual(estimate_purchases(holding, self.HISTORY), [])


class HoldingsTest(unittest.TestCase):
    def test_legacy_single_fund_format_is_sp500(self):
        holdings = parse_holdings({"units": 2_402, "acquisition_amount": 12_000})
        self.assertEqual(holdings, {"sp500": Holding(2_402, 12_000)})

    def test_multi_fund_format(self):
        holdings = parse_holdings(
            {
                "sp500": {"units": 2_402, "acquisition_amount": 12_000, "as_of": "2026-10-05",
                          "monthly": {"amount": 5_000, "day": 10}},
                "オルカン": {"units": 1_322, "acquisition_amount": 5_000,
                          "orders": [{"date": "2026-10-05", "amount": 10_000}]},
            }
        )
        self.assertEqual(holdings["sp500"], Holding(2_402, 12_000, date(2026, 10, 5), 5_000, 10))
        self.assertEqual(
            holdings["allcountry"], Holding(1_322, 5_000, orders=((date(2026, 10, 5), 10_000),))
        )

    def test_rejects_monthly_without_day(self):
        with self.assertRaises(NotifierError):
            parse_holdings({"sp500": {"units": 1, "acquisition_amount": 1, "monthly": {"amount": 5_000}}})

    def test_portfolio_json_env_wins_over_legacy_env(self):
        env = {
            "PORTFOLIO_JSON": json.dumps({"allcountry": {"units": 5, "acquisition_amount": 6}}),
            "PORTFOLIO_UNITS": "1",
            "PORTFOLIO_ACQUISITION_AMOUNT": "2",
        }
        with mock.patch.dict(os.environ, env):
            self.assertEqual(load_holdings(), {"allcountry": Holding(5, 6)})

    def test_legacy_env(self):
        env = {"PORTFOLIO_UNITS": "1", "PORTFOLIO_ACQUISITION_AMOUNT": "2"}
        with mock.patch.dict(os.environ, env), mock.patch.dict(os.environ, {"PORTFOLIO_JSON": ""}):
            self.assertEqual(load_holdings(), {"sp500": Holding(1, 2)})


class BuildReportTest(unittest.TestCase):
    def test_adds_estimated_purchases_to_holding(self):
        holding = Holding(1_000, 5_000, date(2026, 10, 5), 5_000, 10)
        price = FundPrice(date="2026年10月14日", price=37_300, change=100)
        with mock.patch.object(notifier, "fetch_fund_price", return_value=price), mock.patch.object(
            notifier, "fetch_price_history", return_value=EstimatePurchasesTest.HISTORY[:4]
        ):
            report = notifier.build_report(ALLCOUNTRY, holding)
        self.assertEqual(report.portfolio, Portfolio(1_000 + 5_000 * 10_000 // 37_300, 10_000))


class DailyChangeTest(unittest.TestCase):
    def test_units_bought_on_price_date_have_no_daily_change(self):
        report = FundReport(
            ALLCOUNTRY,
            FundPrice(date="2026年10月06日", price=38_000, change=100),
            Portfolio(units=3_631, acquisition_amount=14_000),
            (notifier.Purchase(date(2026, 10, 6), 10_000, 2_631),),
        )
        self.assertEqual(report.daily_change(), 10)


class ToushinCsvTest(unittest.TestCase):
    CSV = (
        "年月日,基準価額(円),純資産総額（百万円）,分配金,決算日\n"
        "2026年08月12日,45301,12840276,,\n"
        "2026年08月13日,45424,12910623,,\n"
        "2026年08月14日,45727,13011097,,\n"
    )

    def test_uses_last_two_rows(self):
        price = parse_toushin_csv(self.CSV)
        self.assertEqual(price.date, "2026年08月14日")
        self.assertEqual(price.price, 45_727)
        self.assertEqual(price.change, 303)

    def test_ignores_trailing_blank_and_broken_rows(self):
        price = parse_toushin_csv(self.CSV + "\n,,,,\n2026年08月17日,,,,\n")
        self.assertEqual(price.date, "2026年08月14日")
        self.assertEqual(price.change, 303)

    def test_rejects_insufficient_rows(self):
        with self.assertRaises(NotifierError):
            parse_toushin_csv("年月日,基準価額(円)\n2026年08月14日,45727\n")


if __name__ == "__main__":
    unittest.main()
