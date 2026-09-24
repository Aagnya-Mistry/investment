# Copyright (c) 2026, Aagnya Mistry and contributors
# See license.txt

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import nowdate

from investment.investment.doctype.investment_holding.test_investment_holding import (
	create_financial_institution,
	create_investment_type,
	make_holding,
)

BANK_ACCOUNT = "_Test Bank - _TC"
HOLDING_ACCOUNTS = {
	"investment_account": "Short-term Investments - _TC",
	"accrued_interest_account": "Earnest Money - _TC",
	"interest_income_account": "Interest on Fixed Deposits - _TC",
	"dividend_income_account": "Interest Income - _TC",
	"realised_gain_loss_account": "Gain/Loss on Asset Disposal - _TC",
	"charges_account": "Bank Charges - _TC",
	"tax_withheld_receivable_account": "Prepaid Expenses - _TC",
}


class TestInvestmentTransaction(ERPNextTestSuite):
	def setUp(self):
		create_investment_type("_Test Bank FD", "Deposit")
		create_investment_type("_Test Equity Fund", "Units")
		create_financial_institution("_Test Bank")

	def test_transaction_needs_approved_holding(self):
		holding = make_holding(**HOLDING_ACCOUNTS).insert()
		transaction = make_transaction(holding.name, "Purchase", gross_amount=1000)
		self.assertRaises(frappe.ValidationError, transaction.insert)

	def test_purchase_posts_gl_and_creates_lot(self):
		holding = make_submitted_holding()
		transaction = make_transaction(holding.name, "Purchase", gross_amount=100000, charges=500).submit()

		self.assertEqual(transaction.net_amount, 100500)
		self.assertEqual(
			get_gl_entries(transaction.name),
			{
				"Short-term Investments - _TC": (100000, 0),
				"Bank Charges - _TC": (500, 0),
				BANK_ACCOUNT: (0, 100500),
			},
		)
		self.assertEqual(
			frappe.db.get_value("Investment Lot", {"investment_transaction": transaction.name}, "amount"),
			100000,
		)

		holding.reload()
		self.assertEqual(holding.total_cost, 100000)
		self.assertEqual(holding.status, "Active")

	def test_purchase_above_approved_amount_is_blocked(self):
		holding = make_submitted_holding(approved_amount=100000)
		make_transaction(holding.name, "Purchase", gross_amount=60000).submit()

		transaction = make_transaction(holding.name, "Additional Purchase", gross_amount=50000)
		self.assertRaises(frappe.ValidationError, transaction.insert)

	def test_sale_uses_fifo_cost(self):
		holding = make_submitted_holding(investment_type="_Test Equity Fund")
		make_transaction(holding.name, "Purchase", units=100, rate=10).submit()
		make_transaction(holding.name, "Additional Purchase", units=100, rate=12).submit()

		sale = make_transaction(holding.name, "Sale", units=150, rate=15).submit()

		# 100 units at 10 + 50 units at 12
		self.assertEqual(sale.cost_of_units_sold, 1600)
		self.assertEqual(sale.realised_gain_loss, 650)
		self.assertEqual(
			get_gl_entries(sale.name),
			{
				BANK_ACCOUNT: (2250, 0),
				"Short-term Investments - _TC": (0, 1600),
				"Gain/Loss on Asset Disposal - _TC": (0, 650),
			},
		)

		holding.reload()
		self.assertEqual(holding.units_held, 50)
		self.assertEqual(holding.total_cost, 600)
		self.assertEqual(holding.status, "Partially Redeemed")

	def test_cannot_sell_more_units_than_held(self):
		holding = make_submitted_holding(investment_type="_Test Equity Fund")
		make_transaction(holding.name, "Purchase", units=100, rate=10).submit()

		sale = make_transaction(holding.name, "Sale", units=101, rate=10)
		self.assertRaises(frappe.ValidationError, sale.insert)

	def test_cancel_purchase_reverses_gl_and_deletes_lot(self):
		holding = make_submitted_holding()
		transaction = make_transaction(holding.name, "Purchase", gross_amount=100000).submit()

		transaction.cancel()

		self.assertFalse(get_gl_entries(transaction.name))
		self.assertFalse(frappe.db.exists("Investment Lot", {"investment_transaction": transaction.name}))
		holding.reload()
		self.assertEqual(holding.total_cost, 0)

	def test_cannot_cancel_purchase_already_sold(self):
		holding = make_submitted_holding(investment_type="_Test Equity Fund")
		purchase = make_transaction(holding.name, "Purchase", units=100, rate=10).submit()
		make_transaction(holding.name, "Sale", units=40, rate=10).submit()

		self.assertRaises(frappe.ValidationError, purchase.cancel)

	def test_interest_accrual_and_receipt(self):
		holding = make_submitted_holding()
		make_transaction(holding.name, "Purchase", gross_amount=100000).submit()

		accrual = make_transaction(
			holding.name,
			"Interest Accrual",
			interest_amount=750,
			accrual_period_from=nowdate(),
			accrual_period_to=nowdate(),
		).submit()
		self.assertEqual(
			get_gl_entries(accrual.name),
			{"Earnest Money - _TC": (750, 0), "Interest on Fixed Deposits - _TC": (0, 750)},
		)
		holding.reload()
		self.assertEqual(holding.accrued_interest, 750)

		receipt = make_transaction(holding.name, "Interest Receipt", interest_amount=750, tax_withheld=75)
		receipt.submit()
		self.assertEqual(
			get_gl_entries(receipt.name),
			{BANK_ACCOUNT: (675, 0), "Prepaid Expenses - _TC": (75, 0), "Earnest Money - _TC": (0, 750)},
		)
		holding.reload()
		self.assertEqual(holding.accrued_interest, 0)

	def test_opening_uses_temporary_opening_account(self):
		holding = make_submitted_holding()
		opening = make_transaction(holding.name, "Opening", gross_amount=100000).submit()

		self.assertEqual(
			get_gl_entries(opening.name),
			{"Short-term Investments - _TC": (100000, 0), "Temporary Opening - _TC": (0, 100000)},
		)
		self.assertIsNone(opening.cash_account)

	def test_deposit_maturity_marks_holding_matured(self):
		holding = make_submitted_holding()
		make_transaction(holding.name, "Purchase", gross_amount=100000).submit()
		maturity = make_transaction(holding.name, "Maturity", gross_amount=100000).submit()

		self.assertEqual(maturity.realised_gain_loss, 0)
		holding.reload()
		self.assertEqual(holding.total_cost, 0)
		self.assertEqual(holding.status, "Matured")


def make_submitted_holding(**args):
	holding = make_holding(**HOLDING_ACCOUNTS)
	holding.update(args)
	return holding.submit()


def make_transaction(investment_holding, transaction_type, **args):
	transaction = frappe.get_doc(
		{
			"doctype": "Investment Transaction",
			"investment_holding": investment_holding,
			"transaction_type": transaction_type,
			"posting_date": nowdate(),
			"cash_account": BANK_ACCOUNT,
		}
	)
	transaction.update(args)
	return transaction


def get_gl_entries(voucher_no):
	entries = frappe.get_all(
		"GL Entry",
		filters={"voucher_type": "Investment Transaction", "voucher_no": voucher_no, "is_cancelled": 0},
		fields=["account", "debit", "credit"],
	)
	return {entry.account: (entry.debit, entry.credit) for entry in entries}
