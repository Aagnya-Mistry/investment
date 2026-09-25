# Copyright (c) 2026, Aagnya Mistry and contributors
# See license.txt

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import add_days, date_diff, getdate, nowdate

from investment.investment.doctype.investment_holding.test_investment_holding import (
	create_financial_institution,
	create_investment_type,
)
from investment.investment.doctype.investment_transaction.test_investment_transaction import (
	make_submitted_holding,
	make_transaction,
)


class TestInvestmentRollover(ERPNextTestSuite):
	def setUp(self):
		create_investment_type("_Test Bank FD", "Deposit")
		create_financial_institution("_Test Bank")

		self.holding = make_submitted_holding()
		make_transaction(self.holding.name, "Purchase", gross_amount=100000).submit()

	def test_rollover_needs_approved_holding(self):
		cancelled_holding = make_submitted_holding()
		cancelled_holding.cancel()

		rollover = make_rollover(cancelled_holding.name, principal_rolled_over=1000)
		self.assertRaises(frappe.ValidationError, rollover.insert)

	def test_rollover_type_is_set_from_instrument(self):
		rollover = make_rollover(self.holding.name, principal_rolled_over=1000).insert()

		self.assertEqual(rollover.rollover_type, "Auto Renewal")

	def test_cannot_roll_over_more_than_paid_out(self):
		make_transaction(self.holding.name, "Maturity", gross_amount=100000).submit()

		rollover = make_rollover(self.holding.name, principal_rolled_over=100001).insert()
		self.assertRaises(frappe.ValidationError, rollover.submit)

	def test_cannot_roll_over_interest_not_received(self):
		make_transaction(self.holding.name, "Maturity", gross_amount=100000).submit()

		rollover = make_rollover(
			self.holding.name, principal_rolled_over=100000, interest_rolled_over=500
		).insert()
		self.assertRaises(frappe.ValidationError, rollover.submit)

	def test_cannot_roll_over_same_proceeds_twice(self):
		make_transaction(self.holding.name, "Maturity", gross_amount=100000).submit()
		make_rollover(self.holding.name, principal_rolled_over=60000).insert().submit()

		rollover = make_rollover(self.holding.name, principal_rolled_over=50000).insert()
		self.assertRaises(frappe.ValidationError, rollover.submit)

	def test_submit_creates_draft_new_holding(self):
		make_transaction(self.holding.name, "Maturity", gross_amount=100000).submit()
		rollover = make_rollover(self.holding.name, principal_rolled_over=100000).insert().submit()

		new_holding = frappe.get_doc("Investment Holding", rollover.new_holding)
		self.assertEqual(new_holding.docstatus, 0)
		self.assertEqual(getdate(new_holding.purchase_date), getdate(rollover.rollover_date))
		self.assertEqual(new_holding.approved_amount, 100000)
		self.assertEqual(new_holding.principal_amount, 100000)
		self.assertEqual(
			date_diff(new_holding.maturity_date, new_holding.purchase_date),
			date_diff(self.holding.maturity_date, self.holding.purchase_date),
		)

	def test_changed_rate_sets_new_terms_changed(self):
		make_transaction(self.holding.name, "Maturity", gross_amount=100000).submit()
		rollover = make_rollover(self.holding.name, principal_rolled_over=100000).insert().submit()

		new_holding = frappe.get_doc("Investment Holding", rollover.new_holding)
		new_holding.rate_of_interest = 8
		new_holding.save()

		self.assertEqual(frappe.db.get_value("Investment Rollover", rollover.name, "new_terms_changed"), 1)

	def test_cancel_deletes_draft_new_holding(self):
		make_transaction(self.holding.name, "Maturity", gross_amount=100000).submit()
		rollover = make_rollover(self.holding.name, principal_rolled_over=100000).insert().submit()
		new_holding = rollover.new_holding

		rollover.cancel()

		self.assertFalse(frappe.db.exists("Investment Holding", new_holding))


def make_rollover(original_holding, **args):
	rollover = frappe.get_doc(
		{
			"doctype": "Investment Rollover",
			"original_holding": original_holding,
			"rollover_date": add_days(nowdate(), 1),
		}
	)
	rollover.update(args)
	return rollover
