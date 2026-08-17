import unittest
from decimal import Decimal

from notifier import FundPrice, Portfolio, build_discord_payload


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


if __name__ == "__main__":
    unittest.main()
