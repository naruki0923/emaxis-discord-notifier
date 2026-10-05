import contextlib
import io
import json
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import portfolio_update
from notifier import NotifierError
from portfolio_update import parse_number

TODAY = date(2026, 10, 5)


class PortfolioUpdateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "portfolio.json"
        patcher = mock.patch.object(portfolio_update, "PORTFOLIO_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def run_update(self, *argv, today=TODAY):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = portfolio_update.main(list(argv), today=today)
        return code, err.getvalue()

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_writes_owner_only_file(self):
        self.assertEqual(self.run_update("sp500", "2,202", "10,000")[0], 0)
        self.assertEqual(self.saved()["sp500"], {"units": 2_202, "acquisition_amount": 10_000, "as_of": "2026-10-05"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["portfolio.json"])

    def test_buy_defaults_to_today_and_is_not_duplicated(self):
        self.run_update("オルカン", "0", "0", "--buy", "10,000")
        self.run_update("オルカン", "0", "0", "--buy", "10,000")
        self.assertEqual(self.saved()["allcountry"]["orders"], [{"date": "2026-10-05", "amount": 10_000}])

    def test_buy_date(self):
        self.run_update("オルカン", "0", "0", "--buy", "10,000", "--buy-date", "2026-10-06")
        self.assertEqual(self.saved()["allcountry"]["orders"], [{"date": "2026-10-06", "amount": 10_000}])

    def test_invalid_buy_date_is_rejected(self):
        code, err = self.run_update("オルカン", "0", "0", "--buy", "10,000", "--buy-date", "10/6")
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", err)
        self.assertFalse(self.path.exists())

    def test_buy_date_without_buy_is_rejected(self):
        code, err = self.run_update("オルカン", "0", "0", "--buy-date", "2026-10-06")
        self.assertEqual(code, 1)
        self.assertIn("--buy と一緒に", err)

    def test_old_buys_are_dropped_after_two_weeks(self):
        self.run_update("オルカン", "0", "0", "--buy", "10,000")
        self.run_update("オルカン", "2,631", "10,000", today=date(2026, 10, 19))
        self.assertIn("orders", self.saved()["allcountry"])
        self.run_update("オルカン", "2,631", "10,000", today=date(2026, 10, 20))
        self.assertNotIn("orders", self.saved()["allcountry"])

    def test_clear_buys(self):
        self.run_update("オルカン", "0", "0", "--buy", "10,000")
        self.run_update("オルカン", "0", "0", "--clear-buys")
        self.assertNotIn("orders", self.saved()["allcountry"])

    def test_monthly_since_is_kept_until_plan_changes(self):
        # 初回登録は前からある積立として扱い、設定日を付けない
        self.run_update("sp500", "1", "1", "--monthly", "5,000", "--day", "10")
        self.assertEqual(self.saved()["sp500"]["monthly"], {"amount": 5_000, "day": 10})
        self.run_update("sp500", "2", "2", "--monthly", "10,000", today=date(2026, 11, 20))
        self.assertEqual(self.saved()["sp500"]["monthly"], {"amount": 10_000, "day": 10, "since": "2026-11-20"})
        # 同じ内容を指定し直しても設定日は変わらない
        self.run_update("sp500", "3", "3", "--monthly", "10,000", "--day", "10", today=date(2026, 12, 20))
        self.assertEqual(self.saved()["sp500"]["monthly"], {"amount": 10_000, "day": 10, "since": "2026-11-20"})

    def test_bad_input_leaves_existing_file(self):
        self.run_update("sp500", "2,202", "10,000")
        before = self.path.read_text(encoding="utf-8")
        self.assertEqual(self.run_update("sp500", "abc", "10,000")[0], 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)


class ParseNumberTest(unittest.TestCase):
    def test_accepts_sbi_style_numbers(self):
        self.assertEqual(parse_number("2,202口"), 2_202)
        self.assertEqual(parse_number("10,000円"), 10_000)

    def test_rejects_non_ascii_digits(self):
        for text in ("²", "１０", "-1", ""):
            with self.subTest(text=text), self.assertRaises(NotifierError):
                parse_number(text)


if __name__ == "__main__":
    unittest.main()
