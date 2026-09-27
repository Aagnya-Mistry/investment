# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries
from erpnext.controllers.accounts_controller import AccountsController
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

from investment.investment.doctype.investment_transaction.investment_transaction import (
	EXIT_TYPES,
	PURCHASE_TYPES,
)

MARKET_VALUATION = "Market Valuation"
IMPAIRMENT_ASSESSMENT = "Impairment Assessment"
OPEN_STATUSES = ("Active", "Partially Redeemed")

# instruments each valuation type applies to
VALUATION_INSTRUMENTS = {
	MARKET_VALUATION: ("Units", "Bond"),
	IMPAIRMENT_ASSESSMENT: ("Deposit", "Bond"),
}

# measurement categories each valuation type applies to, and the ones among them that post GL
VALUATION_CATEGORIES = {
	MARKET_VALUATION: ("Amortized Cost", "FVOCI", "FVTPL"),
	IMPAIRMENT_ASSESSMENT: ("Amortized Cost", "FVOCI"),
}
GL_CATEGORIES = {
	MARKET_VALUATION: ("FVOCI", "FVTPL"),
	IMPAIRMENT_ASSESSMENT: ("Amortized Cost", "FVOCI"),
}


class InvestmentValuation(AccountsController):
	def validate(self):
		self.set_missing_values()
		self.validate_duplicate_holdings()
		for row in self.holdings:
			self.validate_row(row)
			self.set_row_values(row)

		self.set_total_gain_loss()

	def on_submit(self):
		self.make_gl_entries()
		self.update_holdings()

	def before_cancel(self):
		super().before_cancel()
		self.validate_is_latest_valuation()

	def on_cancel(self):
		super().on_cancel()
		self.ignore_linked_doctypes = ("GL Entry",)
		make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)
		self.update_holdings()

	def is_market_valuation(self):
		return self.valuation_type == MARKET_VALUATION

	def get_holding(self, investment_holding):
		if not hasattr(self, "_holdings"):
			self._holdings = {}

		if investment_holding not in self._holdings:
			self._holdings[investment_holding] = frappe.get_doc("Investment Holding", investment_holding)

		return self._holdings[investment_holding]

	def set_missing_values(self):
		if not self.cost_center:
			self.cost_center = frappe.get_cached_value("Company", self.company, "cost_center")

	def validate_duplicate_holdings(self):
		seen = set()
		for row in self.holdings:
			if row.investment_holding in seen:
				frappe.throw(
					_("Row #{0}: Investment Holding {1} is added more than once").format(
						row.idx, frappe.bold(row.investment_holding)
					)
				)
			seen.add(row.investment_holding)

	def validate_row(self, row):
		self.validate_row_holding(row)
		self.validate_row_applies(row)
		self.validate_after_previous_valuation(row)
		self.validate_row_inputs(row)

	def validate_row_holding(self, row):
		holding = self.get_holding(row.investment_holding)
		if holding.docstatus != 1 or holding.company != self.company:
			frappe.throw(
				_(
					"Row #{0}: Investment Holding {1} must be an approved (submitted) holding of Company {2}"
				).format(row.idx, frappe.bold(row.investment_holding), frappe.bold(self.company))
			)

		if getdate(self.valuation_date) < getdate(holding.purchase_date):
			frappe.throw(
				_(
					"Row #{0}: Valuation Date cannot be before the Purchase Date of Investment Holding {1}"
				).format(row.idx, frappe.bold(row.investment_holding))
			)

	def validate_row_applies(self, row):
		holding = self.get_holding(row.investment_holding)
		if holding.instrument_class not in VALUATION_INSTRUMENTS[self.valuation_type]:
			frappe.throw(
				_("Row #{0}: {1} does not apply to {2} instruments").format(
					row.idx, _(self.valuation_type), frappe.bold(holding.instrument_class)
				)
			)

		if holding.measurement_category not in VALUATION_CATEGORIES[self.valuation_type]:
			frappe.throw(
				_("Row #{0}: {1} does not apply to {2} holdings").format(
					row.idx, _(self.valuation_type), frappe.bold(holding.measurement_category)
				)
			)

	def validate_after_previous_valuation(self, row):
		"""Each valuation posts only the change since the previous one, so they must stay in date order."""
		previous_row = get_latest_valuation_row(row.investment_holding, self.valuation_type, self.name)
		if previous_row and getdate(self.valuation_date) <= getdate(previous_row.valuation_date):
			frappe.throw(
				_("Row #{0}: Investment Holding {1} is already valued on {2} in {3}").format(
					row.idx,
					frappe.bold(row.investment_holding),
					frappe.bold(frappe.format_value(previous_row.valuation_date, "Date")),
					frappe.bold(previous_row.parent),
				)
			)

	def validate_row_inputs(self, row):
		for fieldname in ("nav_per_unit", "market_price", "provision_amount"):
			if flt(row.get(fieldname)) < 0:
				frappe.throw(
					_("Row #{0}: {1} cannot be negative").format(row.idx, _(row.meta.get_label(fieldname)))
				)

		if self.is_market_valuation() and flt(row.conversion_rate) <= 0:
			frappe.throw(_("Row #{0}: Exchange Rate must be greater than zero").format(row.idx))

		if not self.is_market_valuation() and not row.ecl_stage:
			frappe.throw(_("Row #{0}: ECL Stage is required").format(row.idx))

	def set_row_values(self, row):
		holding = self.get_holding(row.investment_holding)
		previous_row = get_latest_valuation_row(row.investment_holding, self.valuation_type, self.name)

		row.posts_gl_entry = int(holding.measurement_category in GL_CATEGORIES[self.valuation_type])
		row.book_value = holding.get_ledger_balance(holding.investment_account, self.valuation_date)
		row.previous_amount = flt(previous_row.get(self.get_amount_field())) if previous_row else 0

		if self.is_market_valuation():
			self.set_fair_value(row, holding)
		else:
			self.validate_provision_amount(row)

		new_amount = flt(row.get(self.get_amount_field()))
		row.adjustment_amount = flt(new_amount - row.previous_amount, row.precision("adjustment_amount"))

	def get_amount_field(self):
		return "unrealised_gain_loss" if self.is_market_valuation() else "provision_amount"

	def set_fair_value(self, row, holding):
		row.units_held = flt(get_units_held(holding.name, self.valuation_date), row.precision("units_held"))
		price = row.nav_per_unit if holding.instrument_class == "Units" else row.market_price

		if row.units_held and flt(price) <= 0:
			frappe.throw(
				_("Row #{0}: Please enter the {1} of Investment Holding {2}").format(
					row.idx,
					_("NAV per Unit") if holding.instrument_class == "Units" else _("Market Price"),
					frappe.bold(row.investment_holding),
				)
			)

		fair_value = flt(row.units_held) * flt(price) * flt(row.conversion_rate)
		row.fair_value = flt(fair_value, row.precision("fair_value"))
		row.unrealised_gain_loss = flt(
			row.fair_value - flt(row.book_value), row.precision("unrealised_gain_loss")
		)

	def validate_provision_amount(self, row):
		if flt(row.provision_amount) > flt(row.book_value):
			frappe.throw(
				_("Row #{0}: Provision Amount cannot be more than the Book Value {1}").format(
					row.idx, frappe.bold(frappe.format_value(row.book_value, currency=self.currency))
				)
			)

	def set_total_gain_loss(self):
		"""A higher provision is a loss, so impairment changes count with the opposite sign."""
		sign = 1 if self.is_market_valuation() else -1
		total = sum(flt(row.adjustment_amount) for row in self.holdings) * sign
		self.total_gain_loss = flt(total, self.precision("total_gain_loss"))

	def validate_is_latest_valuation(self):
		for row in self.holdings:
			later_row = get_latest_valuation_row(row.investment_holding, self.valuation_type, self.name)
			if later_row and getdate(later_row.valuation_date) > getdate(self.valuation_date):
				frappe.throw(
					_(
						"Please cancel the later Investment Valuation {0} of Investment Holding {1} first"
					).format(frappe.bold(later_row.parent), frappe.bold(row.investment_holding))
				)

	def update_holdings(self):
		for row in self.holdings:
			self.get_holding(row.investment_holding).update_valuation()

	def make_gl_entries(self):
		make_gl_entries(self.get_gl_entries())

	def get_gl_entries(self):
		gl_entries = []
		for row in self.holdings:
			if not (row.posts_gl_entry and flt(row.adjustment_amount)):
				continue

			for account, debit, credit in self.get_row_entries(row):
				gl_entries.append(self.get_gl_entry(row, account, debit, credit))

		return gl_entries

	def get_row_entries(self, row):
		"""Market: fair value adjustment against unrealised gain/loss. Impairment: loss against provision."""
		holding = self.get_holding(row.investment_holding)
		increase, decrease = max(flt(row.adjustment_amount), 0), max(-flt(row.adjustment_amount), 0)

		if self.is_market_valuation():
			debit_account = holding.get_account("fair_value_adjustment_account")
			credit_account = holding.get_account("unrealised_gain_loss_account")
		else:
			debit_account = holding.get_account("impairment_loss_account")
			credit_account = holding.get_account(get_provision_account_field(holding))

		return [(debit_account, increase, decrease), (credit_account, decrease, increase)]

	def get_gl_entry(self, row, account, debit, credit):
		precision = self.precision("total_gain_loss")
		return self.get_gl_dict(
			{
				"account": account,
				"debit": flt(debit, precision),
				"credit": flt(credit, precision),
				"against": row.investment_holding,
				"cost_center": self.cost_center,
				"finance_book": self.finance_book,
				"posting_date": self.valuation_date,
				"voucher_detail_no": row.name,
			},
			item=row,
		)

	@frappe.whitelist()
	def set_holdings(self):
		"""Fill the table with every holding this run should value, including exited ones still carrying an amount."""
		self.set("holdings", [])
		for investment_holding in get_holdings_to_value(self.company, self.valuation_type):
			if not self.has_amount_to_carry(investment_holding):
				continue

			row = self.append("holdings", {"investment_holding": investment_holding})
			self.set_row_preview(row)

	def has_amount_to_carry(self, investment_holding):
		if frappe.db.get_value("Investment Holding", investment_holding, "status") in OPEN_STATUSES:
			return True

		previous_row = get_latest_valuation_row(investment_holding, self.valuation_type)
		return bool(previous_row and flt(previous_row.get(self.get_amount_field())))

	def set_row_preview(self, row):
		"""Ledger figures shown before the user enters prices; recalculated in full on save."""
		holding = self.get_holding(row.investment_holding)
		previous_row = get_latest_valuation_row(row.investment_holding, self.valuation_type)

		row.update(
			{
				"instrument_class": holding.instrument_class,
				"holding_currency": holding.currency,
				"measurement_category": holding.measurement_category,
				"credit_rating": holding.credit_rating,
				"book_value": holding.get_ledger_balance(holding.investment_account, self.valuation_date),
				"previous_amount": flt(previous_row.get(self.get_amount_field())) if previous_row else 0,
			}
		)
		if self.is_market_valuation():
			row.units_held = flt(
				get_units_held(holding.name, self.valuation_date), row.precision("units_held")
			)
		else:
			self.set_previous_assessment(row, holding, previous_row)

	def set_previous_assessment(self, row, holding, previous_row):
		"""Start from the last assessment so only what changed needs editing; exited holdings go to zero."""
		row.ecl_stage = previous_row.ecl_stage if previous_row else "Stage 1"
		if previous_row and holding.status in OPEN_STATUSES:
			row.provision_amount = previous_row.provision_amount


def get_provision_account_field(holding):
	"""FVOCI holdings are already at fair value, so their provision sits in OCI instead of reducing the asset."""
	if holding.measurement_category == "FVOCI":
		return "unrealised_gain_loss_account"

	return "impairment_provision_account"


def get_holdings_to_value(company, valuation_type):
	return frappe.get_list(
		"Investment Holding",
		filters={
			"company": company,
			"docstatus": 1,
			"instrument_class": ("in", VALUATION_INSTRUMENTS[valuation_type]),
			"measurement_category": ("in", VALUATION_CATEGORIES[valuation_type]),
		},
		pluck="name",
		order_by="name asc",
	)


def get_units_held(investment_holding, upto):
	"""Units bought minus units exited up to a date, from submitted transactions."""
	transaction = frappe.qb.DocType("Investment Transaction")

	def get_units(transaction_types):
		units = (
			frappe.qb.from_(transaction)
			.select(Sum(transaction.units))
			.where(transaction.investment_holding == investment_holding)
			.where(transaction.docstatus == 1)
			.where(transaction.transaction_type.isin(transaction_types))
			.where(transaction.posting_date <= upto)
		).run()
		return flt(units[0][0])

	return get_units(PURCHASE_TYPES) - get_units(EXIT_TYPES)


def get_latest_valuation_row(investment_holding, valuation_type, exclude_valuation=None):
	"""Row of the holding's latest submitted valuation of this type, optionally ignoring one valuation."""
	valuation = frappe.qb.DocType("Investment Valuation")
	row = frappe.qb.DocType("Investment Valuation Holding")
	query = (
		frappe.qb.from_(row)
		.join(valuation)
		.on(row.parent == valuation.name)
		.select(
			row.parent,
			valuation.valuation_date,
			row.unrealised_gain_loss,
			row.provision_amount,
			row.ecl_stage,
		)
		.where(row.parenttype == "Investment Valuation")
		.where(row.investment_holding == investment_holding)
		.where(valuation.docstatus == 1)
		.where(valuation.valuation_type == valuation_type)
		.orderby(valuation.valuation_date, order=frappe.qb.desc)
		.orderby(valuation.creation, order=frappe.qb.desc)
		.limit(1)
	)
	if exclude_valuation:
		query = query.where(valuation.name != exclude_valuation)

	result = query.run(as_dict=True)
	return result[0] if result else None
