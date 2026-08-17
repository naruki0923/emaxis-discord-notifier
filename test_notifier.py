import unittest
from decimal import Decimal

from notifier import FundPrice, NotifierError, Portfolio, build_discord_payload, parse_toushin_csv


class FundPriceTest(unittest.TestCase):
    def test_positive_change_percent_uses_previous_price(self):
        price = FundPrice(date="2026年08月14日", price=45_727, change=303)
        self.assertEqual(price.previous_price, 45_424)
        self.assertEqual(price.change_percent, Decimal("0.67"))

    def test_negative_message(self):
        price = FundPrice(date="2026年08月14日", price=9_900, change=-100)
        embed = build_discord_payload(price)["embeds"][0]
        self.assertEqual(embed["description"], "📉 値下がり")
        self.assertEqual(embed["fields"][1]["value"], "**-100円（-1.00%）**")

    def test_unchanged_message(self):
        price = FundPrice(date="2026年08月14日", price=10_000, change=0)
        embed = build_discord_payload(price)["embeds"][0]
        self.assertEqual(embed["description"], "➡️ 変わらず")

    def test_portfolio_matches_sbi_screenshot(self):
        price = FundPrice(date="2026年08月14日", price=45_727, change=303)
        portfolio = Portfolio(units=2_202, acquisition_amount=10_000)
        self.assertEqual(portfolio.valuation(price), 10_069)
        self.assertEqual(portfolio.daily_change(price), 67)
        self.assertEqual(portfolio.profit(price), 69)
        self.assertEqual(portfolio.profit_percent(price), Decimal("0.69"))

        fields = build_discord_payload(price, portfolio)["embeds"][0]["fields"]
        self.assertEqual(fields[2]["value"], "**10,069円**")
        self.assertEqual(fields[3]["value"], "**+67円**")
        self.assertEqual(fields[4]["value"], "**+69円（+0.69%）**")


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
