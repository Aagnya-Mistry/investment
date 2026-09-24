# Copyright (c) 2026, Aagnya Mistry and contributors
# See license.txt

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import add_days, nowdate

TEST_COMPANY = "_Test Company"


class TestInvestmentHolding(ERPNextTestSuite):
	def setUp(self):
		create_investment_type("_Test Bank FD", "Deposit")
		create_financial_institution("_Test Bank")

	def test_maturity_date_must_be_after_purchase_date(self):
		holding = make_holding(maturity_date=nowdate())
		self.assertRaises(frappe.ValidationError, holding.insert)

	# only for now in version 1.0, floating rate is not supported
	def test_floating_rate_is_rejected(self):
		holding = make_holding(rate_type="Floating")
		self.assertRaises(frappe.ValidationError, holding.insert)

	def test_approved_amount_must_be_positive(self):
		holding = make_holding(approved_amount=0)
		self.assertRaises(frappe.ValidationError, holding.insert)

	def test_account_must_belong_to_company(self):
		other_account = frappe.db.get_value(
			"Account", {"company": ("!=", TEST_COMPANY), "is_group": 0}, "name"
		)
		holding = make_holding(investment_account=other_account)
		self.assertRaises(frappe.ValidationError, holding.insert)

	def test_inactive_investment_type_is_rejected(self):
		create_investment_type("_Test Inactive Type", "Deposit", is_active=0)
		holding = make_holding(investment_type="_Test Inactive Type")
		self.assertRaises(frappe.ValidationError, holding.insert)

	def test_status_follows_submit_and_cancel(self):
		holding = make_holding().insert()
		self.assertEqual(holding.status, "Draft")

		holding.submit()
		self.assertEqual(holding.status, "Active")
		self.assertEqual(holding.approved_by, frappe.session.user)

		holding.cancel()
		self.assertEqual(holding.status, "Cancelled")


def make_holding(**args):
	holding = frappe.get_doc(
		{
			"doctype": "Investment Holding",
			"investment_type": "_Test Bank FD",
			"company": TEST_COMPANY,
			"issuer": "_Test Bank",
			"purchase_date": nowdate(),
			"maturity_date": add_days(nowdate(), 365),
			"rate_type": "Fixed",
			"rate_of_interest": 7.5,
			"interest_payout_type": "Cumulative",
			"compounding_frequency": "Quarterly",
			"principal_amount": 100000,
			"measurement_category": "Amortized Cost",
			"investment_account": get_company_account(),
			"approved_amount": 100000,
			"investment_rationale": "Park surplus cash",
		}
	)
	holding.update(args)
	return holding


def get_company_account():
	return frappe.db.get_value("Account", {"company": TEST_COMPANY, "is_group": 0}, "name")


def create_investment_type(name, instrument_class, is_active=1):
	if frappe.db.exists("Investment Type", name):
		return

	frappe.get_doc(
		{
			"doctype": "Investment Type",
			"investment_type": name,
			"instrument_class": instrument_class,
			"is_active": is_active,
		}
	).insert()


def create_financial_institution(name):
	if frappe.db.exists("Financial Institution", name):
		return

	frappe.get_doc(
		{"doctype": "Financial Institution", "institution_name": name, "institution_type": "Bank"}
	).insert()
