# Copyright (c) 2026, Aagnya Mistry and contributors
# See license.txt

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import add_days, nowdate

from investment.investment.doctype.investment_holding.test_investment_holding import (
	TEST_COMPANY,
	create_financial_institution,
	create_investment_type,
)
from investment.investment.doctype.investment_transaction.test_investment_transaction import (
	make_submitted_holding,
	make_transaction,
)

FAIR_VALUE_ACCOUNT = "Earnest Money - _TC"
UNREALISED_ACCOUNT = "Revaluation Surplus - _TC"
IMPAIRMENT_LOSS_ACCOUNT = "Impairment - _TC"
IMPAIRMENT_PROVISION_ACCOUNT = "Short-term Provisions - _TC"
VALUATION_ACCOUNTS = {
	"fair_value_adjustment_account": FAIR_VALUE_ACCOUNT,
	"unrealised_gain_loss_account": UNREALISED_ACCOUNT,
	"impairment_loss_account": IMPAIRMENT_LOSS_ACCOUNT,
	"impairment_provision_account": IMPAIRMENT_PROVISION_ACCOUNT,
}


class TestInvestmentValuation(ERPNextTestSuite):
	def setUp(self):
		create_investment_type("_Test Bank FD", "Deposit")
		create_investment_type("_Test Equity Fund", "Units")
		create_financial_institution("_Test Bank")

	def test_market_valuation_posts_unrealised_gain(self):
		holding = make_fund_holding("FVTPL")
		valuation = make_market_valuation(holding.name, nav_per_unit=12).submit()

		row = valuation.holdings[0]
		self.assertEqual((row.book_value, row.fair_value, row.unrealised_gain_loss), (1000, 1200, 200))
		self.assertEqual(
			get_gl_entries(valuation.name), {FAIR_VALUE_ACCOUNT: (200, 0), UNREALISED_ACCOUNT: (0, 200)}
		)

		holding.reload()
		self.assertEqual((holding.unrealised_gain_loss, holding.market_value), (200, 1200))

	def test_next_valuation_posts_only_the_change(self):
		holding = make_fund_holding("FVOCI")
		make_market_valuation(holding.name, nav_per_unit=12).submit()

		valuation = make_market_valuation(holding.name, nav_per_unit=11, days=1).submit()

		self.assertEqual(valuation.holdings[0].previous_amount, 200)
		self.assertEqual(valuation.holdings[0].adjustment_amount, -100)
		self.assertEqual(
			get_gl_entries(valuation.name), {UNREALISED_ACCOUNT: (100, 0), FAIR_VALUE_ACCOUNT: (0, 100)}
		)

	def test_amortized_cost_market_valuation_posts_no_gl(self):
		holding = make_fund_holding("Amortized Cost")
		valuation = make_market_valuation(holding.name, nav_per_unit=12).submit()

		self.assertEqual(valuation.holdings[0].posts_gl_entry, 0)
		self.assertEqual(get_gl_entries(valuation.name), {})
		self.assertEqual(frappe.db.get_value("Investment Holding", holding.name, "unrealised_gain_loss"), 200)

	def test_market_valuation_does_not_apply_to_deposits(self):
		holding = make_deposit_holding("Amortized Cost")
		valuation = make_market_valuation(holding.name)
		self.assertRaises(frappe.ValidationError, valuation.insert)

	def test_impairment_posts_provision(self):
		holding = make_deposit_holding("Amortized Cost")
		valuation = make_impairment(holding.name, provision_amount=1000).submit()

		self.assertEqual(valuation.total_gain_loss, -1000)
		self.assertEqual(
			get_gl_entries(valuation.name),
			{IMPAIRMENT_LOSS_ACCOUNT: (1000, 0), IMPAIRMENT_PROVISION_ACCOUNT: (0, 1000)},
		)
		self.assertEqual(
			frappe.db.get_value("Investment Holding", holding.name, "impairment_provision"), 1000
		)

	def test_fvoci_impairment_is_booked_in_unrealised_account(self):
		holding = make_deposit_holding("FVOCI")
		valuation = make_impairment(holding.name, provision_amount=1000).submit()

		self.assertEqual(
			get_gl_entries(valuation.name),
			{IMPAIRMENT_LOSS_ACCOUNT: (1000, 0), UNREALISED_ACCOUNT: (0, 1000)},
		)

	def test_provision_cannot_exceed_book_value(self):
		holding = make_deposit_holding("Amortized Cost")
		valuation = make_impairment(holding.name, provision_amount=100001)
		self.assertRaises(frappe.ValidationError, valuation.insert)

	def test_valuation_cannot_be_back_dated(self):
		holding = make_fund_holding("FVTPL")
		make_market_valuation(holding.name, nav_per_unit=12, days=1).submit()

		valuation = make_market_valuation(holding.name, nav_per_unit=11)
		self.assertRaises(frappe.ValidationError, valuation.insert)

	def test_only_latest_valuation_can_be_cancelled(self):
		holding = make_fund_holding("FVTPL")
		first = make_market_valuation(holding.name, nav_per_unit=12).submit()
		second = make_market_valuation(holding.name, nav_per_unit=15, days=1).submit()

		self.assertRaises(frappe.ValidationError, first.cancel)

		second.cancel()
		self.assertEqual(get_gl_entries(second.name), {})
		self.assertEqual(frappe.db.get_value("Investment Holding", holding.name, "unrealised_gain_loss"), 200)

	def test_get_holdings_clears_gain_of_exited_holding(self):
		holding = make_fund_holding("FVTPL")
		make_market_valuation(holding.name, nav_per_unit=12).submit()
		make_transaction(holding.name, "Sale", units=100, rate=12).submit()

		valuation = frappe.get_doc(
			{
				"doctype": "Investment Valuation",
				"company": TEST_COMPANY,
				"valuation_type": "Market Valuation",
				"valuation_date": add_days(nowdate(), 1),
			}
		)
		valuation.set_holdings()
		self.assertIn(holding.name, [row.investment_holding for row in valuation.holdings])

		valuation.set(
			"holdings", [row for row in valuation.holdings if row.investment_holding == holding.name]
		)
		valuation.insert().submit()

		self.assertEqual(valuation.holdings[0].adjustment_amount, -200)
		self.assertEqual(frappe.db.get_value("Investment Holding", holding.name, "unrealised_gain_loss"), 0)


def make_fund_holding(measurement_category):
	"""Equity fund holding of 100 units bought at 10."""
	holding = make_submitted_holding(
		investment_type="_Test Equity Fund", measurement_category=measurement_category, **VALUATION_ACCOUNTS
	)
	make_transaction(holding.name, "Purchase", units=100, rate=10).submit()
	return holding


def make_deposit_holding(measurement_category):
	holding = make_submitted_holding(measurement_category=measurement_category, **VALUATION_ACCOUNTS)
	make_transaction(holding.name, "Purchase", gross_amount=100000).submit()
	return holding


def make_market_valuation(investment_holding, days=0, **row):
	return make_valuation("Market Valuation", investment_holding, days, row)


def make_impairment(investment_holding, days=0, **row):
	row.setdefault("ecl_stage", "Stage 2")
	return make_valuation("Impairment Assessment", investment_holding, days, row)


def make_valuation(valuation_type, investment_holding, days, row):
	return frappe.get_doc(
		{
			"doctype": "Investment Valuation",
			"company": TEST_COMPANY,
			"valuation_type": valuation_type,
			"valuation_date": add_days(nowdate(), days),
			"holdings": [{"investment_holding": investment_holding, **row}],
		}
	)


def get_gl_entries(voucher_no):
	entries = frappe.get_all(
		"GL Entry",
		filters={"voucher_type": "Investment Valuation", "voucher_no": voucher_no, "is_cancelled": 0},
		fields=["account", "debit", "credit"],
	)
	return {entry.account: (entry.debit, entry.credit) for entry in entries}
