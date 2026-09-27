# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

"""Interest and bond amortisation maths for Deposit and Bond holdings.

Every period here is half-open: `start` is included and `end` is not, so each day earns interest
exactly once. A purchase earns interest from its purchase date; an exit stops interest on its
posting date.
"""

from itertools import pairwise

import frappe
from frappe.utils import add_days, add_months, date_diff, flt, getdate

INTEREST_CLASSES = ("Deposit", "Bond")
FREQUENCY_MONTHS = {"Monthly": 1, "Quarterly": 3, "Semi-Annual": 6, "Annual": 12}
DEFAULT_PERIOD_MONTHS = 12


class InterestCalculator:
	def __init__(self, holding):
		self.holding = holding
		self.is_bond = holding.instrument_class == "Bond"
		self.maturity_date = getdate(holding.maturity_date)
		self.rate = flt(holding.coupon_rate if self.is_bond else holding.rate_of_interest) / 100
		self.unit_value = flt(holding.face_value) if self.is_bond else 1
		self.period_months = FREQUENCY_MONTHS.get(self.get_frequency(), DEFAULT_PERIOD_MONTHS)
		self.lots = get_lot_timelines(holding.name, self.is_bond)

	def get_frequency(self):
		if self.is_bond:
			return self.holding.coupon_frequency

		if self.holding.interest_payout_type == "Non-Cumulative":
			return self.holding.payout_frequency

		return self.holding.compounding_frequency

	# Divide the investment's lifetime into interest-calculation periods.
	def get_schedule_periods(self):
		"""One period per payout / compounding / coupon period, from the first purchase until maturity or full exit."""
		periods = []
		start, end = self.get_start_date(), self.get_end_date()
		while start and start < end:
			period_end = min(self.get_period_end(start), end)
			periods.append((start, period_end))
			start = period_end

		return periods

	# Find where the holding's interest period containing `date` ends.
	def get_period_end(self, date):
		if self.get_frequency() == "At Maturity":
			return self.maturity_date

		return self.get_reference_period(date)[1]

	def get_start_date(self):
		return min((lot.purchase_date for lot in self.lots), default=None)

	def get_end_date(self):
		"""Maturity, or the day the last lot was fully exited if that is earlier."""
		if any(lot.get_quantity(self.maturity_date) for lot in self.lots):
			return self.maturity_date

		exit_dates = [posting_date for lot in self.lots for posting_date, _quantity in lot.exits]
		return min(max(exit_dates, default=self.maturity_date), self.maturity_date)

	def get_interest(self, start, end):
		interest = 0
		for piece_start, piece_end in self.split_at_balance_changes(start, end):
			balance = sum(lot.get_quantity(piece_start) for lot in self.lots) * self.unit_value
			interest += balance * self.rate * self.get_year_fraction(piece_start, piece_end)

		return interest

	def get_amortisation(self, start, end):
		"""Bond premium (negative) or discount (positive) earned over the period, effective interest method."""
		if not self.is_bond:
			return 0

		amortisation = 0
		for piece_start, piece_end in self.split_at_balance_changes(start, end):
			for lot in self.lots:
				if not lot.get_quantity(piece_start):
					continue

				value_change = self.get_carrying_value(lot, piece_end) - self.get_carrying_value(
					lot, piece_start
				)
				amortisation += lot.get_quantity(piece_start) * value_change

		return amortisation

	def get_carrying_cost(self, units_already_sold, units_to_sell, posting_date):
		"""Amortised cost of bond units being sold, taken from the oldest lots (FIFO)."""
		cost = 0
		for lot in self.lots:
			skipped = min(lot.quantity, units_already_sold)
			units_already_sold -= skipped
			taken = min(lot.quantity - skipped, units_to_sell)
			units_to_sell -= taken
			cost += taken * self.get_carrying_value(lot, getdate(posting_date))

		return cost

	def split_at_balance_changes(self, start, end):
		start, end = getdate(start), getdate(end)
		change_dates = {date for lot in self.lots for date in lot.get_change_dates() if start < date < end}
		boundaries = [start, *sorted(change_dates), end]

		return list(pairwise(boundaries))

	def get_year_fraction(self, start, end):
		convention = self.holding.day_count_convention
		if convention == "Actual/360":
			return date_diff(end, start) / 360

		if convention == "30/360":
			return get_days_30_360(start, end) / 360

		if convention == "Actual/Actual (ICMA)":
			return self.get_icma_fraction(start, end)

		return date_diff(end, start) / 365

	def get_icma_fraction(self, start, end):
		"""Days in each reference period, divided by (days in that period * periods per year)."""
		fraction, periods_per_year = 0, 12 / self.period_months
		while start < end:
			period_start, period_end = self.get_reference_period(start)
			piece_end = min(period_end, end)
			fraction += date_diff(piece_end, start) / (date_diff(period_end, period_start) * periods_per_year)
			start = piece_end

		return fraction

	def get_reference_period(self, date):
		"""Interest period containing `date` (before maturity), counted back from the maturity date."""
		count = 1
		while getdate(add_months(self.maturity_date, -count * self.period_months)) > date:
			count += 1

		return (
			getdate(add_months(self.maturity_date, -count * self.period_months)),
			getdate(add_months(self.maturity_date, -(count - 1) * self.period_months)),
		)

	def get_carrying_value(self, lot, date):
		"""Amortised (clean) cost of one bond unit of `lot` on `date`."""
		if date >= self.maturity_date:
			return self.unit_value

		if lot.effective_rate is None:
			lot.effective_rate = self.get_effective_rate(lot)

		return self.get_present_value(lot.effective_rate, date) - self.get_accrued_coupon(date)

	def get_effective_rate(self, lot):
		"""Annual rate at which the lot's future cash flows discount back to its purchase price (bisection)."""
		target = lot.cost / lot.quantity + self.get_accrued_coupon(lot.purchase_date)
		low, high = -0.99, 10.0
		for _iteration in range(200):
			rate = (low + high) / 2
			if self.get_present_value(rate, lot.purchase_date) > target:
				low = rate
			else:
				high = rate

		return (low + high) / 2

	def get_present_value(self, effective_rate, date):
		value = 0
		for payment_date, amount in self.get_coupon_payments():
			if payment_date > date:
				value += amount * (1 + effective_rate) ** (-date_diff(payment_date, date) / 365)

		return value

	def get_coupon_payments(self):
		"""Coupon (plus face value at maturity) paid per bond unit, as (date, amount)."""
		if getattr(self, "_coupon_payments", None) is None:
			payments = [(end, self.get_coupon(start, end)) for start, end in self.get_coupon_periods()]
			payments[-1] = (self.maturity_date, payments[-1][1] + self.unit_value)
			self._coupon_payments = payments

		return self._coupon_payments

	def get_coupon_periods(self):
		if getattr(self, "_coupon_periods", None) is None:
			self._coupon_periods = self.make_coupon_periods()

		return self._coupon_periods

	def make_coupon_periods(self):
		first_date = min(self.get_start_date(), getdate(self.holding.purchase_date))
		if self.holding.coupon_frequency == "At Maturity":
			return [(first_date, self.maturity_date)]

		periods = [self.get_reference_period(add_days(self.maturity_date, -1))]
		while periods[0][0] > first_date:
			periods.insert(0, self.get_reference_period(add_days(periods[0][0], -1)))

		return periods

	def get_coupon(self, start, end):
		return self.unit_value * self.rate * self.get_year_fraction(start, end)

	def get_accrued_coupon(self, date):
		"""Coupon earned per bond unit since the last coupon date, not yet paid."""
		for start, end in self.get_coupon_periods():
			if start <= date < end:
				return self.get_coupon(start, date)

		return 0


class LotTimeline:
	"""Quantity (bond units, or deposit principal) held in one lot over time."""

	def __init__(self, lot, quantity):
		self.purchase_date = getdate(lot.purchase_date)
		self.quantity = quantity
		self.cost = flt(lot.amount)
		self.exits = []
		self.effective_rate = None

	def get_quantity(self, date):
		if date < self.purchase_date:
			return 0

		return self.quantity - sum(quantity for posting_date, quantity in self.exits if posting_date <= date)

	def get_change_dates(self):
		return [self.purchase_date, *(posting_date for posting_date, _quantity in self.exits)]

	def consume(self, posting_date, quantity):
		taken = min(self.get_quantity(posting_date), quantity)
		if taken > 0:
			self.exits.append((posting_date, taken))

		return max(taken, 0)


def get_lot_timelines(investment_holding, is_bond):
	"""Lots of the holding, with each exit taken from the oldest lot first (FIFO)."""
	from investment.investment.doctype.investment_transaction.investment_transaction import get_lots

	quantity_field, exit_field = ("units", "units") if is_bond else ("amount", "gross_amount")
	lots = [LotTimeline(lot, flt(lot.get(quantity_field))) for lot in get_lots(investment_holding)]

	for exit_transaction in get_exit_transactions(investment_holding):
		remaining = flt(exit_transaction.get(exit_field))
		for lot in lots:
			remaining -= lot.consume(getdate(exit_transaction.posting_date), remaining)

	return lots


def get_exit_transactions(investment_holding):
	from investment.investment.doctype.investment_transaction.investment_transaction import EXIT_TYPES

	return frappe.get_all(
		"Investment Transaction",
		filters={
			"investment_holding": investment_holding,
			"docstatus": 1,
			"transaction_type": ("in", EXIT_TYPES),
		},
		fields=["posting_date", "units", "gross_amount"],
		order_by="posting_date asc, creation asc",
	)


def get_days_30_360(start, end):
	"""Day count under the 30/360 (bond basis) convention."""
	start_day = min(start.day, 30)
	end_day = 30 if end.day == 31 and start_day == 30 else end.day

	return (end.year - start.year) * 360 + (end.month - start.month) * 30 + (end_day - start_day)
