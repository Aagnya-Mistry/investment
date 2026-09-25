# Copyright (c) 2026, Aagnya Mistry and contributors
# See license.txt

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import flt, getdate

from investment.investment.doctype.investment_holding.test_investment_holding import (
	create_financial_institution,
	create_investment_type,
)
from investment.investment.doctype.investment_interest_accrual.investment_interest_accrual import (
	accrue_interest,
	get_schedule,
	get_schedule_name,
)
from investment.investment.doctype.investment_transaction.test_investment_transaction import (
	get_gl_entries,
	make_submitted_holding,
	make_transaction,
)
from investment.investment.interest import get_days_30_360

FD_TERMS = {
	"purchase_date": "2026-01-01",
	"maturity_date": "2027-01-01",
	"rate_of_interest": 7.3,
	"interest_payout_type": "Non-Cumulative",
	"payout_frequency": "Quarterly",
}
BOND_TERMS = {
	"investment_type": "_Test Corporate Bond",
	"purchase_date": "2026-01-01",
	"maturity_date": "2028-01-01",
	"face_value": 1000,
	"coupon_rate": 8,
	"coupon_frequency": "Semi-Annual",
}


class TestInvestmentInterestAccrual(ERPNextTestSuite):
	def setUp(self):
		create_investment_type("_Test Bank FD", "Deposit")
		create_investment_type("_Test Corporate Bond", "Bond")
		create_financial_institution("_Test Bank")

	def test_purchase_creates_schedule_by_payout_frequency(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)

		schedule = get_schedule(holding.name)
		self.assertEqual(len(schedule.periods), 4)
		self.assertEqual(getdate(schedule.periods[0].period_to), getdate("2026-03-31"))
		self.assertEqual(getdate(schedule.periods[-1].period_to), getdate("2026-12-31"))
		# first quarter: 100000 * 7.3% * 90 / 365
		self.assertEqual(schedule.periods[0].interest_amount, 1800)
		self.assertEqual(sum(period.interest_amount for period in schedule.periods), 7300)

	def test_interest_needs_a_deposit_first(self):
		holding = make_submitted_holding(**FD_TERMS)

		accrual = make_accrual(holding.name, "2026-01-01", "2026-01-31")
		self.assertRaises(frappe.ValidationError, accrual.insert)

	def test_accrue_interest_posts_each_period_once(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)

		transactions = accrue_interest(holding.name, "2026-06-30")
		self.assertEqual(len(transactions), 2)
		self.assertEqual(accrue_interest(holding.name, "2026-06-30"), [])
		self.assertEqual(
			get_gl_entries(transactions[0]),
			{"Earnest Money - _TC": (1800, 0), "Interest on Fixed Deposits - _TC": (0, 1800)},
		)

		schedule = get_schedule(holding.name)
		self.assertEqual(getdate(schedule.accrued_upto), getdate("2026-06-30"))
		self.assertEqual([period.status for period in schedule.periods], ["Posted"] * 2 + ["Pending"] * 2)

	def test_daily_job_posts_only_ended_periods(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)

		transactions = accrue_interest(holding.name, "2026-05-15", full_periods_only=True)
		self.assertEqual(len(transactions), 1)
		self.assertEqual(getdate(get_schedule(holding.name).accrued_upto), getdate("2026-03-31"))

	def test_partial_period_is_completed_by_next_accrual(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)

		accrue_interest(holding.name, "2026-01-31")
		self.assertEqual(get_schedule(holding.name).periods[0].status, "Partially Posted")

		accrue_interest(holding.name, "2026-03-31")
		first_quarter = get_schedule(holding.name).periods[0]
		self.assertEqual((first_quarter.posted_amount, first_quarter.status), (1800, "Posted"))

	def test_withdrawal_accrues_first_and_updates_same_schedule(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)
		schedule_name = get_schedule_name(holding.name)

		make_deposit(holding.name, "Withdrawal", "2026-02-01", 40000)

		schedule = get_schedule(holding.name)
		self.assertEqual(schedule.name, schedule_name)
		self.assertEqual(getdate(schedule.accrued_upto), getdate("2026-01-31"))
		# January on 100000 (620) + 1 Feb to 31 Mar on the remaining 60000 (708)
		self.assertEqual(schedule.periods[0].interest_amount, 1328)
		self.assertEqual(schedule.periods[0].status, "Partially Posted")

	def test_principal_cannot_change_inside_accrued_period(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 50000)
		accrue_interest(holding.name, "2026-03-31")

		purchase = make_deposit(holding.name, "Additional Purchase", "2026-03-15", 10000, submit=False)
		self.assertRaises(frappe.ValidationError, purchase.insert)

	def test_only_latest_accrual_can_be_cancelled(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)
		first_quarter, second_quarter = accrue_interest(holding.name, "2026-06-30")

		first_accrual = frappe.get_doc("Investment Transaction", first_quarter)
		self.assertRaises(frappe.ValidationError, first_accrual.cancel)

		frappe.get_doc("Investment Transaction", second_quarter).cancel()
		schedule = get_schedule(holding.name)
		self.assertEqual(getdate(schedule.accrued_upto), getdate("2026-03-31"))
		self.assertEqual(schedule.periods[1].status, "Pending")

	def test_accrual_period_cannot_overlap(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)
		accrue_interest(holding.name, "2026-03-31")

		accrual = make_accrual(holding.name, "2026-03-20", "2026-03-31")
		self.assertRaises(frappe.ValidationError, accrual.insert)

	def test_accrual_period_must_fit_one_schedule_period(self):
		holding = make_submitted_holding(**FD_TERMS)
		make_deposit(holding.name, "Purchase", "2026-01-01", 100000)

		accrual = make_accrual(holding.name, "2026-03-20", "2026-04-10")
		self.assertRaises(frappe.ValidationError, accrual.insert)

	def test_bond_discount_is_amortised_to_face_value(self):
		holding = make_submitted_holding(**BOND_TERMS)
		make_transaction(holding.name, "Purchase", posting_date="2026-01-01", units=10, rate=950).submit()

		schedule = get_schedule(holding.name)
		self.assertEqual(flt(sum(period.amortisation_amount for period in schedule.periods), 2), 500)

		maturity = make_transaction(holding.name, "Maturity", posting_date="2028-01-01", units=10, rate=1000)
		maturity.submit()

		self.assertEqual(maturity.cost_of_units_sold, 10000)
		self.assertEqual(maturity.realised_gain_loss, 0)
		holding.reload()
		self.assertEqual(holding.total_cost, 0)

	def test_30_360_day_count(self):
		# every month counts as 30 days, and the 31st is treated as the 30th
		self.assertEqual(get_days_30_360(getdate("2026-02-01"), getdate("2026-03-01")), 30)
		self.assertEqual(get_days_30_360(getdate("2026-01-31"), getdate("2026-03-31")), 60)
		self.assertEqual(get_days_30_360(getdate("2026-01-01"), getdate("2027-01-01")), 360)


def make_deposit(investment_holding, transaction_type, posting_date, gross_amount, submit=True):
	transaction = make_transaction(
		investment_holding, transaction_type, posting_date=posting_date, gross_amount=gross_amount
	)
	return transaction.submit() if submit else transaction


def make_accrual(investment_holding, period_from, period_to):
	return make_transaction(
		investment_holding,
		"Interest Accrual",
		posting_date=period_to,
		interest_amount=100,
		accrual_period_from=period_from,
		accrual_period_to=period_to,
	)
