# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate, nowdate

from investment.investment.interest import INTEREST_CLASSES, InterestCalculator


class InvestmentInterestAccrual(Document):
	def validate(self):
		self.validate_instrument_class()
		self.validate_single_schedule()
		self.set_status()

	# Check whether the Investment Holding's instrument class is allowed for interest accrual.
	def validate_instrument_class(self):
		if self.instrument_class not in INTEREST_CLASSES:
			frappe.throw(_("Interest schedules are only made for Deposit and Bond holdings"))

	# Make sure an Investment Holding has only one Interest Accrual schedule.
	def validate_single_schedule(self):
		schedule_name = get_schedule_name(self.investment_holding)
		if schedule_name and schedule_name != self.name:
			frappe.throw(
				_("Investment Holding {0} already has an interest schedule {1}").format(
					frappe.bold(self.investment_holding), frappe.bold(schedule_name)
				)
			)

	# A schedule with no periods left (every purchase cancelled) is marked Cancelled.
	def set_status(self):
		self.status = "Active" if self.periods else "Cancelled"

	# Find how much interest has already been posted for every scheduled period and update the schedule.
	def set_posted_amounts(self):
		accruals = get_submitted_accruals(self.investment_holding)
		for period in self.periods:
			period_accruals = [accrual for accrual in accruals if is_in_period(accrual, period)]
			period.posted_amount = flt(
				sum(flt(accrual.interest_amount) for accrual in period_accruals),
				period.precision("posted_amount"),
			)
			period.investment_transaction = period_accruals[-1].name if period_accruals else None
			period.status = get_period_status(period)

		self.accrued_upto = max((accrual.accrual_period_to for accrual in accruals), default=None)


# Find how many decimal places should be used for interest amounts.
def get_amount_precision():
	return frappe.get_precision("Investment Transaction", "interest_amount")


# Check whether an interest transaction belongs to a particular schedule period.
def is_in_period(accrual, period):
	return getdate(period.period_from) <= getdate(accrual.accrual_period_from) <= getdate(period.period_to)


# Determine whether a schedule period is Pending, Partially Posted, or Posted.
def get_period_status(period):
	if not flt(period.posted_amount):
		return "Pending"

	if flt(period.posted_amount) < flt(period.interest_amount):
		return "Partially Posted"

	return "Posted"


# Create the holding's Interest Accrual schedule, or rebuild its periods in place if it already exists.
def update_schedule(investment_holding):
	"""Rebuild the schedule periods from the holding's current lots and exits; called when principal changes."""
	holding = frappe.get_doc("Investment Holding", investment_holding)
	periods = get_schedule_periods(holding)
	schedule = get_schedule(investment_holding)

	if not schedule:
		if not periods:
			return None

		schedule = frappe.get_doc(
			{"doctype": "Investment Interest Accrual", "investment_holding": holding.name}
		)

	schedule.set("periods", periods)
	schedule.set_posted_amounts()
	schedule.flags.ignore_permissions = True  # system-managed record, users only have read access
	schedule.save()

	return schedule


# Generate all the periods for which interest/amortisation should be calculated.
def get_schedule_periods(holding):
	calculator = InterestCalculator(holding)
	periods = []
	for start, end in calculator.get_schedule_periods():
		interest = flt(calculator.get_interest(start, end), get_amount_precision())
		amortisation = get_rounded_amortisation(calculator, start, end)
		if interest or amortisation:
			periods.append(
				{
					"period_from": start,
					"period_to": add_days(end, -1),
					"interest_amount": interest,
					"amortisation_amount": amortisation,
				}
			)

	return periods


# Refresh the schedule's posted amounts after an Interest Accrual transaction is submitted or cancelled.
def update_posted_amounts(investment_holding):
	schedule = get_schedule(investment_holding)
	if not schedule:
		return

	schedule.set_posted_amounts()
	schedule.flags.ignore_permissions = True  # system-managed record, users only have read access
	schedule.save()


def get_schedule_name(investment_holding):
	return frappe.db.get_value("Investment Interest Accrual", {"investment_holding": investment_holding})


def get_schedule(investment_holding):
	schedule_name = get_schedule_name(investment_holding)
	return frappe.get_doc("Investment Interest Accrual", schedule_name) if schedule_name else None


# Find the schedule period containing `date`, if the holding has one.
def get_schedule_period(investment_holding, date):
	return frappe.db.get_value(
		"Investment Interest Accrual Period",
		{
			"parent": get_schedule_name(investment_holding),
			"parenttype": "Investment Interest Accrual",
			"period_from": ("<=", date),
			"period_to": (">=", date),
		},
		["period_from", "period_to"],
		as_dict=True,
	)


# Find the date until which interest has already been accrued.
def get_accrued_upto(investment_holding):
	return frappe.db.get_value(
		"Investment Interest Accrual", {"investment_holding": investment_holding}, "accrued_upto"
	)


# Get all submitted Interest Accrual transactions for an Investment Holding.
def get_submitted_accruals(investment_holding):
	return frappe.get_all(
		"Investment Transaction",
		filters={
			"investment_holding": investment_holding,
			"transaction_type": "Interest Accrual",
			"docstatus": 1,
		},
		fields=["name", "interest_amount", "accrual_period_from", "accrual_period_to"],
		order_by="accrual_period_from asc",
	)


# Actually calculate and post interest for an Investment Holding up to a specified date.
def accrue_interest(investment_holding, upto, full_periods_only=False):
	"""Post Interest Accrual transactions for every unposted day up to `upto`. Safe to run repeatedly.

	With `full_periods_only`, a period is posted only once it has fully ended (used by the daily job).
	"""
	schedule = get_schedule(investment_holding)
	if not schedule:
		return []

	calculator = InterestCalculator(frappe.get_doc("Investment Holding", investment_holding))
	start = add_days(schedule.accrued_upto, 1) if schedule.accrued_upto else None
	upto = getdate(upto)

	transactions = []
	for period in schedule.periods:
		if full_periods_only and getdate(period.period_to) > upto:
			break

		transaction = post_period(calculator, period, start, upto)
		if transaction:
			transactions.append(transaction.name)

	return transactions


# Calculate and post the unposted portion of one particular schedule period.
def post_period(calculator, period, start, upto):
	"""Post the unposted part of one schedule period that falls on or before `upto`."""
	period_from = max(getdate(period.period_from), getdate(start or period.period_from))
	period_to = min(getdate(period.period_to), upto)
	if period_from > period_to:
		return None

	end = add_days(period_to, 1)
	if period_to == getdate(period.period_to):
		interest = flt(period.interest_amount) - flt(period.posted_amount)
	else:
		interest = calculator.get_interest(period_from, end)

	if flt(interest, get_amount_precision()) <= 0:
		return None

	return make_accrual_transaction(
		calculator.holding,
		period_from,
		period_to,
		interest,
		get_rounded_amortisation(calculator, period_from, end),
	)


# Calculate the amortisation for a particular period while handling rounding correctly.
def get_rounded_amortisation(calculator, start, end):
	"""Rounded as a running total, so the pieces add up to exactly the whole premium / discount."""
	first_date, precision = calculator.get_start_date(), get_amount_precision()
	return flt(calculator.get_amortisation(first_date, end), precision) - flt(
		calculator.get_amortisation(first_date, start), precision
	)


# Create and submit an Investment Transaction representing an interest accrual.
def make_accrual_transaction(holding, period_from, period_to, interest, amortisation):
	transaction = frappe.get_doc(
		{
			"doctype": "Investment Transaction",
			"investment_holding": holding.name,
			"transaction_type": "Interest Accrual",
			"posting_date": period_to,
			"accrual_period_from": period_from,
			"accrual_period_to": period_to,
			"interest_amount": flt(interest, get_amount_precision()),
			"amortisation_amount": flt(amortisation, get_amount_precision()),
			"remarks": _("Interest accrued automatically from the interest schedule"),
		}
	)
	transaction.insert()
	transaction.submit()

	return transaction


@frappe.whitelist()
def accrue_interest_upto(investment_holding: str, upto: str):
	frappe.get_doc("Investment Holding", investment_holding).check_permission("read")
	frappe.has_permission("Investment Transaction", "submit", throw=True)

	return accrue_interest(investment_holding, upto)


# Find all Investment Holdings that need interest accrual and process them.
def accrue_interest_for_all_holdings():
	"""Daily job: post every schedule period that ended before today."""
	upto = add_days(nowdate(), -1)
	schedules = frappe.get_all(
		"Investment Interest Accrual",
		filters={"status": "Active"},
		or_filters=[["accrued_upto", "is", "not set"], ["accrued_upto", "<", upto]],
		pluck="investment_holding",
	)

	for investment_holding in schedules:
		accrue_interest_for_holding(investment_holding, upto)


# Accrue interest for one holding while isolating database failures.
def accrue_interest_for_holding(investment_holding, upto):
	"""Accrue one holding in its own database transaction, so one failure does not stop the others."""
	try:
		accrue_interest(investment_holding, upto, full_periods_only=True)
		frappe.db.commit()  # nosemgrep: scheduled job, commit each holding separately
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=_("Interest accrual failed for {0}").format(investment_holding))
