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
    schedule_order,
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

    def test_matches_sbi_screen_on_2026_10_05(self):
        # SBI証券: 評価額 9,833円 / 評価損益 -167円（-1.67%）/ 前日比 +32円
        price = FundPrice(date="2026年10月02日", price=44_658, change=144)
        portfolio = Portfolio(units=2_202, acquisition_amount=10_000)
        self.assertEqual(portfolio.valuation(price), 9_833)
        self.assertEqual(portfolio.profit(price), -167)
        self.assertEqual(portfolio.profit_percent(price), Decimal("-1.67"))
        self.assertEqual(portfolio.daily_change(price), 32)

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
                Portfolio(units=2_202, acquisition_amount=10_000),
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
            "全体資産：14,833円\n"
            "S＆P500：9,833円\n"
            "オルカン：5,000円\n"
            "\n"
            "・前営業日比\n"
            "S＆P500：+32円\n"
            "オルカン：-7円\n"
            "\n"
            "・損益\n"
            "投資金額：15,000円\n"
            "資産：14,833円",
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
        self.assertIn("全体資産：10,000円（オルカンを除く）", message)
        self.assertIn("投資金額：10,000円（オルカンを除く）", message)
        self.assertIn("資産：10,000円（オルカンを除く）", message)

        message = build_message(reports[:1] + [FundReport(ALLCOUNTRY, reports[0].price)])
        self.assertIn("オルカン：保有情報が未設定です", message)
        self.assertIn("投資金額：10,000円\n", message)
        self.assertNotIn("除く", message)


class ScheduleOrderTest(unittest.TestCase):
    def test_weekday_order(self):
        purchase = schedule_order(SP500, date(2026, 10, 5), 10_000)
        self.assertEqual(purchase.order_date, date(2026, 10, 5))
        self.assertEqual(purchase.trade_date, date(2026, 10, 6))
        self.assertEqual(purchase.settlement_date, date(2026, 10, 9))  # 申込日から5営業日目

    def test_allcountry_settles_on_sixth_business_day(self):
        purchase = schedule_order(ALLCOUNTRY, date(2026, 10, 5), 10_000)
        self.assertEqual(purchase.trade_date, date(2026, 10, 6))
        self.assertEqual(purchase.settlement_date, date(2026, 10, 13))  # 10/12 は祝日

    def test_weekend_and_japanese_holiday_move_order_date(self):
        # 2026/10/10(土)・11(日)・12(月・祝)
        purchase = schedule_order(SP500, date(2026, 10, 10), 5_000)
        self.assertEqual(purchase.order_date, date(2026, 10, 13))
        self.assertEqual(purchase.trade_date, date(2026, 10, 14))

    def test_us_market_holiday_is_not_an_order_day(self):
        # 2026/11/26 は感謝祭（ニューヨーク証券取引所が休み）
        purchase = schedule_order(SP500, date(2026, 11, 26), 5_000)
        self.assertEqual(purchase.order_date, date(2026, 11, 27))

    def test_allcountry_also_skips_new_york_bank_hong_kong_and_london_holidays(self):
        # 2026/2/17〜19 は旧正月（香港）。S&P500 は申し込める。
        self.assertEqual(schedule_order(SP500, date(2026, 2, 17), 1).order_date, date(2026, 2, 17))
        self.assertEqual(schedule_order(ALLCOUNTRY, date(2026, 2, 17), 1).order_date, date(2026, 2, 20))
        # 2026/8/31 はロンドンのバンクホリデー
        self.assertEqual(schedule_order(ALLCOUNTRY, date(2026, 8, 31), 1).order_date, date(2026, 9, 1))
        # 2026/11/11 はベテランズデー（ニューヨークの銀行休業日）
        self.assertEqual(schedule_order(SP500, date(2026, 11, 11), 1).order_date, date(2026, 11, 11))
        self.assertEqual(schedule_order(ALLCOUNTRY, date(2026, 11, 11), 1).order_date, date(2026, 11, 12))

    def test_year_end_is_not_a_business_day(self):
        purchase = schedule_order(SP500, date(2026, 12, 30), 1)
        self.assertEqual(purchase.trade_date, date(2027, 1, 4))


class EstimatePurchasesTest(unittest.TestCase):
    HISTORY = [
        (date(2026, 10, 5), 37_900),
        (date(2026, 10, 6), 38_000),
        (date(2026, 10, 7), 38_100),
        (date(2026, 10, 8), 37_000),
        (date(2026, 10, 9), 37_100),
        (date(2026, 10, 13), 37_200),
        (date(2026, 10, 14), 37_300),
    ]

    def test_monthly_and_one_off_orders(self):
        holding = Holding(0, 0, date(2026, 10, 5), 5_000, 10, orders=((date(2026, 10, 5), 10_000),))
        purchases = estimate_purchases(ALLCOUNTRY, holding, self.HISTORY, date(2026, 11, 30))
        self.assertEqual(
            [(p.trade_date, p.amount, p.price, p.units) for p in purchases],
            [
                (date(2026, 10, 6), 10_000, 38_000, 10_000 * 10_000 // 38_000),
                (date(2026, 10, 14), 5_000, 37_300, 5_000 * 10_000 // 37_300),
                (date(2026, 11, 11), 5_000, None, 0),  # 約定待ち
            ],
        )

    def test_skips_purchases_already_in_sbi_values(self):
        # SBI証券は約定日の翌日に保有へ反映する
        holding = Holding(0, 0, date(2026, 10, 15), 5_000, 10)
        purchases = estimate_purchases(ALLCOUNTRY, holding, self.HISTORY, date(2026, 10, 31))
        self.assertEqual(purchases, [])

    def test_update_on_trade_date_still_counts_that_purchase(self):
        holding = Holding(0, 0, date(2026, 10, 14), 5_000, 10)
        purchases = estimate_purchases(ALLCOUNTRY, holding, self.HISTORY, date(2026, 10, 31))
        self.assertEqual([p.trade_date for p in purchases], [date(2026, 10, 14)])

        holding = Holding(0, 0, date(2026, 10, 6), orders=((date(2026, 10, 5), 10_000),))
        purchases = estimate_purchases(ALLCOUNTRY, holding, self.HISTORY, date(2026, 10, 31))
        self.assertEqual([p.trade_date for p in purchases], [date(2026, 10, 6)])

    def test_no_purchases_before_monthly_plan_was_set(self):
        # 10/14 に「毎月10日」を設定。10/13申込・10/14約定の回は存在しない。
        holding = Holding(0, 0, date(2026, 10, 14), 5_000, 10, monthly_since=date(2026, 10, 14))
        purchases = estimate_purchases(ALLCOUNTRY, holding, self.HISTORY, date(2026, 11, 30))
        self.assertEqual([p.order_date for p in purchases], [date(2026, 11, 10)])

        # 10/5 に設定した場合は 10/13 申込の回から
        holding = Holding(0, 0, date(2026, 10, 5), 5_000, 10, monthly_since=date(2026, 10, 5))
        purchases = estimate_purchases(ALLCOUNTRY, holding, self.HISTORY, date(2026, 11, 30))
        self.assertEqual([p.order_date for p in purchases], [date(2026, 10, 13), date(2026, 11, 10)])

    def test_previous_month_order_carried_over_past_as_of(self):
        # 積立日30日: 12/30申込→12/31〜1/3休業→1/4約定。1/2時点の登録でも12月分を数える。
        holding = Holding(0, 0, date(2027, 1, 2), 5_000, 30)
        history = [(date(2026, 12, 30), 40_000), (date(2027, 1, 4), 40_100)]
        purchases = estimate_purchases(SP500, holding, history, date(2027, 1, 10))
        self.assertEqual([(p.order_date, p.trade_date) for p in purchases], [(date(2026, 12, 30), date(2027, 1, 4))])

    def test_waits_until_trade_price_is_published(self):
        holding = Holding(0, 0, date(2026, 10, 5), 5_000, 10)
        purchases = estimate_purchases(ALLCOUNTRY, holding, self.HISTORY[:5], date(2026, 10, 31))
        self.assertEqual([(p.trade_date, p.price) for p in purchases], [(date(2026, 10, 14), None)])

    def test_missing_trade_price_is_an_error(self):
        holding = Holding(0, 0, date(2026, 10, 5), 5_000, 10)
        history = [h for h in self.HISTORY if h[0] != date(2026, 10, 14)] + [(date(2026, 10, 15), 1)]
        with self.assertRaises(NotifierError):
            estimate_purchases(ALLCOUNTRY, holding, history, date(2026, 10, 31), published_until=date(2026, 10, 15))

    def test_lagging_published_data_leaves_purchase_pending(self):
        # 投資信託協会は10/9まで、公式サイトの最新は10/15。10/14約定分は約定待ちのまま。
        holding = Holding(0, 0, date(2026, 10, 5), 5_000, 10)
        history = self.HISTORY[:5] + [(date(2026, 10, 15), 37_400)]
        purchases = estimate_purchases(
            ALLCOUNTRY, holding, history, date(2026, 10, 31), published_until=date(2026, 10, 9)
        )
        self.assertEqual([(p.trade_date, p.price) for p in purchases], [(date(2026, 10, 14), None)])

    def test_no_estimate_without_plan(self):
        holding = Holding(units=1_000, acquisition_amount=5_000, as_of=date(2026, 10, 5))
        self.assertEqual(estimate_purchases(SP500, holding, self.HISTORY, date(2026, 12, 31)), [])


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
        since = parse_holdings(
            {"sp500": {"units": 1, "acquisition_amount": 1, "monthly": {"amount": 5_000, "day": 10, "since": "2026-10-05"}}}
        )["sp500"].monthly_since
        self.assertEqual(since, date(2026, 10, 5))
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
        history = EstimatePurchasesTest.HISTORY[:6]  # 投資信託協会は10/14分がまだ無い
        with mock.patch.object(notifier, "fetch_fund_price", return_value=price), mock.patch.object(
            notifier, "fetch_price_history", return_value=history
        ):
            report = notifier.build_report(ALLCOUNTRY, holding, today=date(2026, 10, 15))
        self.assertEqual(report.portfolio, Portfolio(1_000 + 5_000 * 10_000 // 37_300, 10_000))
        # 約定した当日の口数は前営業日比に含めない
        self.assertEqual(report.daily_change(), 1_000 * 37_300 // 10_000 - 1_000 * 37_200 // 10_000)

    def test_same_day_units_already_in_sbi_values(self):
        # 10/14約定分を含むSBIの表示値を10/15に登録。基準日10/14の前営業日比にはその口数を含めない。
        bought = 5_000 * 10_000 // 37_300
        holding = Holding(1_000 + bought, 10_000, date(2026, 10, 15), 5_000, 10)
        price = FundPrice(date="2026年10月14日", price=37_300, change=100)
        with mock.patch.object(notifier, "fetch_fund_price", return_value=price), mock.patch.object(
            notifier, "fetch_price_history", return_value=EstimatePurchasesTest.HISTORY
        ):
            report = notifier.build_report(ALLCOUNTRY, holding, today=date(2026, 10, 15))
        self.assertEqual(report.portfolio, Portfolio(1_000 + bought, 10_000))
        self.assertEqual(report.daily_change(), 1_000 * 37_300 // 10_000 - 1_000 * 37_200 // 10_000)

    def test_history_failure_still_reports_fund(self):
        holding = Holding(1_000, 5_000, date(2026, 10, 5), 5_000, 10)
        price = FundPrice(date="2026年10月14日", price=37_300, change=100)
        with mock.patch.object(notifier, "fetch_fund_price", return_value=price), mock.patch.object(
            notifier, "fetch_price_history", side_effect=NotifierError("通信に失敗しました")
        ), mock.patch("sys.stderr"):
            report = notifier.build_report(ALLCOUNTRY, holding, today=date(2026, 10, 15))
        # 最新の基準価額（10/14）で約定した分は推定できる
        self.assertEqual(report.portfolio, Portfolio(1_000 + 5_000 * 10_000 // 37_300, 10_000))


class ToushinFetchTest(unittest.TestCase):
    def test_csv_is_downloaded_once_per_fund(self):
        notifier.fetch_toushin_text.cache_clear()
        csv = ToushinCsvTest.CSV.encode("cp932")
        with mock.patch.object(notifier, "request_bytes", return_value=csv) as request, mock.patch.object(
            notifier, "fetch_fund_price_from_mufg", side_effect=NotifierError("403")
        ):
            notifier.fetch_fund_price(SP500)
            notifier.fetch_price_history(SP500)
        notifier.fetch_toushin_text.cache_clear()
        self.assertEqual(request.call_count, 1)


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
