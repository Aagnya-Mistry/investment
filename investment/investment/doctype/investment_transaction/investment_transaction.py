# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from erpnext.accounts.doctype.opening_invoice_creation_tool.opening_invoice_creation_tool import (
	get_temporary_opening_account,
)
from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries
from erpnext.controllers.accounts_controller import AccountsController
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

PURCHASE_TYPES = ("Opening", "Purchase", "Additional Purchase")
EXIT_TYPES = ("Sale", "Redemption", "Withdrawal", "Maturity")
INFLOW_TYPES = (*EXIT_TYPES, "Interest Receipt", "Dividend")
NON_CASH_TYPES = ("Opening", "Interest Accrual")
UNIT_CLASSES = ("Units", "Bond")

# transaction types that only make sense for some instruments; types not listed are allowed for all
ALLOWED_INSTRUMENTS = {
	"Sale": UNIT_CLASSES,
	"Redemption": UNIT_CLASSES,
	"Withdrawal": ("Deposit",),
	"Maturity": ("Deposit", "Bond"),
	"Interest Accrual": ("Deposit", "Bond"),
	"Interest Receipt": ("Deposit", "Bond"),
	"Dividend": ("Units",),
}


class InvestmentTransaction(AccountsController):
	def validate(self):
		self.validate_holding()
		self.validate_transaction_type()
		self.validate_dates()
		self.set_missing_values()
		self.validate_amounts()
		self.set_net_amount()
		self.validate_cash_account()
		self.validate_approved_amount()
		self.validate_available_balance()

	def before_submit(self):
		self.set_cost_and_realised_gain_loss()

	def on_submit(self):
		self.create_lot()
		self.make_gl_entries()
		self.update_holding()

	def before_cancel(self):
		super().before_cancel()
		self.validate_purchase_not_consumed()

	def on_cancel(self):
		super().on_cancel()
		self.ignore_linked_doctypes = ("GL Entry", "Investment Lot")
		self.delete_lot()
		make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)
		self.update_holding()

	def get_holding(self):
		if not getattr(self, "_holding", None):
			self._holding = frappe.get_doc("Investment Holding", self.investment_holding)

		return self._holding

	def validate_holding(self):
		if self.get_holding().docstatus != 1:
			frappe.throw(
				_("Investment Holding {0} must be approved (submitted) before posting transactions").format(
					frappe.bold(self.investment_holding)
				)
			)

	def validate_transaction_type(self):
		allowed = ALLOWED_INSTRUMENTS.get(self.transaction_type)
		if allowed and self.instrument_class not in allowed:
			frappe.throw(
				_("Transaction Type {0} is not allowed for {1} instruments").format(
					frappe.bold(self.transaction_type), frappe.bold(self.instrument_class)
				)
			)

	def validate_dates(self):
		if getdate(self.posting_date) < getdate(self.get_holding().purchase_date):
			frappe.throw(_("Posting Date cannot be before the holding's Purchase Date"))

		if self.transaction_type != "Interest Accrual":
			return

		if not (self.accrual_period_from and self.accrual_period_to):
			frappe.throw(_("Accrual Period From and Accrual Period To are required"))

		if getdate(self.accrual_period_to) < getdate(self.accrual_period_from):
			frappe.throw(_("Accrual Period To cannot be before Accrual Period From"))

	def set_missing_values(self):
		if not self.cost_center:
			self.cost_center = frappe.get_cached_value("Company", self.company, "cost_center")

		if self.is_unit_movement():
			self.gross_amount = flt(flt(self.units) * flt(self.rate), self.precision("gross_amount"))

	def is_unit_movement(self):
		return self.instrument_class in UNIT_CLASSES and self.transaction_type in (
			*PURCHASE_TYPES,
			*EXIT_TYPES,
		)

	def validate_amounts(self):
		for fieldname in self.get_amount_fields():
			if flt(self.get(fieldname)) < 0:
				frappe.throw(_("{0} cannot be negative").format(_(self.meta.get_label(fieldname))))

		if self.is_unit_movement() and (flt(self.units) <= 0 or flt(self.rate) <= 0):
			frappe.throw(_("Units and NAV / Rate must be greater than zero"))

		if flt(self.accrued_interest_in_purchase) > flt(self.gross_amount):
			frappe.throw(_("Accrued Interest in Purchase cannot be more than Gross Amount"))

	def get_amount_fields(self):
		return (
			"units",
			"rate",
			"gross_amount",
			"charges",
			"tax_withheld",
			"interest_amount",
			"accrued_interest_in_purchase",
			"penalty_applied",
		)

	def set_net_amount(self):
		self.net_amount = flt(self.get_net_amount(), self.precision("net_amount"))

		if self.net_amount <= 0:
			frappe.throw(_("Net Amount must be greater than zero"))

	def get_net_amount(self):
		"""Cash that actually moves in (or out of) the bank; for non-cash types, the amount booked."""
		if self.transaction_type in ("Purchase", "Additional Purchase"):
			return flt(self.gross_amount) + flt(self.charges)

		if self.transaction_type == "Opening":
			return flt(self.gross_amount)

		if self.transaction_type == "Interest Accrual":
			return flt(self.interest_amount)

		if self.transaction_type == "Charges":
			return flt(self.charges)

		deductions = flt(self.charges) + flt(self.tax_withheld) + flt(self.penalty_applied)
		return self.get_inflow_amount() - deductions

	def get_inflow_amount(self):
		if self.transaction_type == "Interest Receipt":
			return flt(self.interest_amount)

		return flt(self.gross_amount)

	def validate_cash_account(self):
		if self.transaction_type in NON_CASH_TYPES:
			self.cash_account = None
			return

		if not self.cash_account:
			frappe.throw(_("Cash / Bank Account is required"))

		if frappe.get_cached_value("Account", self.cash_account, "company") != self.company:
			frappe.throw(
				_("Account {0} does not belong to Company {1}").format(
					frappe.bold(self.cash_account), frappe.bold(self.company)
				)
			)

	def validate_approved_amount(self):
		if self.transaction_type not in PURCHASE_TYPES:
			return

		approved_amount = flt(self.get_holding().approved_amount)
		total_purchased = self.get_submitted_total("gross_amount", PURCHASE_TYPES) + flt(self.gross_amount)

		if total_purchased > approved_amount:
			frappe.throw(
				_(
					"Total purchases {0} would exceed the Approved Amount {1} of Investment Holding {2}"
				).format(
					frappe.bold(total_purchased),
					frappe.bold(approved_amount),
					frappe.bold(self.investment_holding),
				)
			)

	def get_submitted_total(self, fieldname, transaction_types):
		"""Sum of a field over the other submitted transactions of this holding."""
		transaction = frappe.qb.DocType("Investment Transaction")
		total = (
			frappe.qb.from_(transaction)
			.select(Sum(transaction[fieldname]))
			.where(transaction.investment_holding == self.investment_holding)
			.where(transaction.docstatus == 1)
			.where(transaction.transaction_type.isin(transaction_types))
			.where(transaction.name != (self.name or ""))
		).run()

		return flt(total[0][0])

	def validate_available_balance(self):
		if self.transaction_type not in EXIT_TYPES:
			return

		if self.instrument_class in UNIT_CLASSES:
			self.validate_balance("units", _("Units"), flt(self.units))
		else:
			self.validate_balance("amount", _("Principal"), flt(self.gross_amount))

	def validate_balance(self, lot_field, label, required):
		purchased = sum(flt(lot.get(lot_field)) for lot in get_lots(self.investment_holding))
		exit_field = "units" if lot_field == "units" else "gross_amount"
		available = purchased - self.get_submitted_total(exit_field, EXIT_TYPES)

		if required > flt(available, self.precision(exit_field)):
			frappe.throw(
				_("{0} available in Investment Holding {1} is {2}, cannot exit {3}").format(
					label, frappe.bold(self.investment_holding), frappe.bold(available), frappe.bold(required)
				)
			)

	def set_cost_and_realised_gain_loss(self):
		if self.transaction_type not in EXIT_TYPES:
			return

		if self.instrument_class in UNIT_CLASSES:
			units_already_sold = self.get_submitted_total("units", EXIT_TYPES)
			cost = get_fifo_cost(get_lots(self.investment_holding), units_already_sold, flt(self.units))
		else:
			cost = flt(self.gross_amount)

		self.cost_of_units_sold = flt(cost, self.precision("cost_of_units_sold"))
		self.realised_gain_loss = flt(
			flt(self.gross_amount) - self.cost_of_units_sold, self.precision("realised_gain_loss")
		)

	def validate_purchase_not_consumed(self):
		if self.transaction_type not in PURCHASE_TYPES:
			return

		lot_field, exit_field = (
			("units", "units") if self.instrument_class in UNIT_CLASSES else ("amount", "gross_amount")
		)
		other_lots = [
			lot for lot in get_lots(self.investment_holding) if lot.investment_transaction != self.name
		]
		remaining = sum(flt(lot.get(lot_field)) for lot in other_lots)

		if flt(remaining - self.get_submitted_total(exit_field, EXIT_TYPES), self.precision(exit_field)) < 0:
			frappe.throw(_("Cannot cancel this purchase because it has already been sold or withdrawn"))

	def create_lot(self):
		if self.transaction_type not in PURCHASE_TYPES:
			return

		lot = frappe.get_doc(
			{
				"doctype": "Investment Lot",
				"investment_holding": self.investment_holding,
				"investment_transaction": self.name,
				"purchase_date": self.posting_date,
				"units": flt(self.units) if self.instrument_class in UNIT_CLASSES else 0,
				"rate": flt(self.rate),
				"amount": self.get_investment_amount(),
			}
		)
		lot.flags.ignore_permissions = True  # system-created record, users only have read access
		lot.insert()

	def delete_lot(self):
		for lot_name in frappe.get_all(
			"Investment Lot", filters={"investment_transaction": self.name}, pluck="name"
		):
			frappe.delete_doc("Investment Lot", lot_name, ignore_permissions=True)

	def get_investment_amount(self):
		"""Part of a purchase that is the cost of the investment itself (excludes bought-in interest)."""
		if self.transaction_type == "Opening":
			return flt(self.gross_amount)

		return flt(self.gross_amount) - flt(self.accrued_interest_in_purchase)

	def update_holding(self):
		self.get_holding().update_position()

	def make_gl_entries(self):
		make_gl_entries(self.get_gl_entries())

	def get_gl_entries(self):
		builders = {
			"Opening": self.get_opening_gl_entries,
			"Purchase": self.get_purchase_gl_entries,
			"Additional Purchase": self.get_purchase_gl_entries,
			"Interest Accrual": self.get_interest_accrual_gl_entries,
			"Interest Receipt": self.get_income_receipt_gl_entries,
			"Dividend": self.get_income_receipt_gl_entries,
			"Charges": self.get_charges_gl_entries,
		}
		builder = builders.get(self.transaction_type, self.get_exit_gl_entries)

		return [
			self.get_gl_entry(account, debit, credit)
			for account, debit, credit in builder()
			if debit or credit
		]

	def get_opening_gl_entries(self):
		opening_account = get_temporary_opening_account(self.company)
		return [
			(self.get_holding_account("investment_account"), self.gross_amount, 0),
			(opening_account, 0, self.gross_amount),
		]

	def get_purchase_gl_entries(self):
		entries = [
			(self.get_holding_account("investment_account"), self.get_investment_amount(), 0),
			(self.cash_account, 0, self.net_amount),
		]

		if flt(self.accrued_interest_in_purchase):
			entries.append(
				(self.get_holding_account("accrued_interest_account"), self.accrued_interest_in_purchase, 0)
			)

		return entries + self.get_charges_and_tax_entries()

	def get_interest_accrual_gl_entries(self):
		return [
			(self.get_holding_account("accrued_interest_account"), self.interest_amount, 0),
			(self.get_holding_account("interest_income_account"), 0, self.interest_amount),
		]

	def get_income_receipt_gl_entries(self):
		if self.transaction_type == "Dividend":
			income_account = self.get_holding_account("dividend_income_account")
		else:
			income_account = self.get_holding_account("accrued_interest_account")

		return [
			(self.cash_account, self.net_amount, 0),
			(income_account, 0, self.get_inflow_amount()),
			*self.get_charges_and_tax_entries(),
		]

	def get_charges_gl_entries(self):
		return [
			(self.get_holding_account("charges_account"), self.charges, 0),
			(self.cash_account, 0, self.charges),
		]

	def get_exit_gl_entries(self):
		entries = [
			(self.cash_account, self.net_amount, 0),
			(self.get_holding_account("investment_account"), 0, self.cost_of_units_sold),
			*self.get_charges_and_tax_entries(),
		]

		if flt(self.realised_gain_loss):
			gain_loss_account = self.get_holding_account("realised_gain_loss_account")
			entries.append(
				(gain_loss_account, max(-self.realised_gain_loss, 0), max(self.realised_gain_loss, 0))
			)

		return entries

	def get_charges_and_tax_entries(self):
		entries = []
		charges = flt(self.charges) + flt(self.penalty_applied)

		if charges:
			entries.append((self.get_holding_account("charges_account"), charges, 0))

		if flt(self.tax_withheld):
			entries.append(
				(self.get_holding_account("tax_withheld_receivable_account"), self.tax_withheld, 0)
			)

		return entries

	def get_holding_account(self, fieldname):
		holding = self.get_holding()
		account = holding.get(fieldname)

		if not account:
			frappe.throw(
				_("Please set {0} in Investment Holding {1}").format(
					frappe.bold(_(holding.meta.get_label(fieldname))), frappe.bold(holding.name)
				)
			)

		return account

	def get_gl_entry(self, account, debit, credit):
		precision = self.precision("net_amount")
		return self.get_gl_dict(
			{
				"account": account,
				"debit": flt(flt(debit) * flt(self.conversion_rate), precision),
				"credit": flt(flt(credit) * flt(self.conversion_rate), precision),
				"against": self.investment_holding,
				"cost_center": self.cost_center,
				"is_opening": "Yes" if self.transaction_type == "Opening" else "No",
			},
			item=self,
		)


def get_lots(investment_holding):
	"""Lots of a holding in FIFO order (oldest first)."""
	return frappe.get_all(
		"Investment Lot",
		filters={"investment_holding": investment_holding},
		fields=["name", "investment_transaction", "units", "amount", "units_remaining"],
		order_by="purchase_date asc, creation asc",
	)


def get_fifo_cost(lots, units_already_sold, units_to_sell):
	"""Cost of `units_to_sell`, taken from the oldest lots after skipping units sold earlier."""
	cost = 0
	for lot in lots:
		if not flt(lot.units):
			continue

		skipped = min(flt(lot.units), units_already_sold)
		units_already_sold -= skipped
		taken = min(flt(lot.units) - skipped, units_to_sell)
		units_to_sell -= taken
		cost += taken * flt(lot.amount) / flt(lot.units)

	return cost
