# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name, get_workflow_state_field
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

WORKFLOW_DRAFT_STATES = ("Pending Approval", "Rejected")
ACCOUNT_FIELDS = (
	"investment_account",
	"accrued_interest_account",
	"interest_income_account",
	"dividend_income_account",
	"realised_gain_loss_account",
	"unrealised_gain_loss_account",
	"fair_value_adjustment_account",
	"tax_withheld_receivable_account",
	"charges_account",
)


class InvestmentHolding(Document):
	def validate(self):
		self.validate_investment_type_is_active()
		self.validate_institutions_are_active()
		self.validate_dates()
		self.validate_rate_type()
		self.validate_amounts()
		self.validate_company_links()
		self.set_status()

	def before_submit(self):
		self.approved_by = frappe.session.user

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def validate_investment_type_is_active(self):
		if self.docstatus == 0 and not frappe.db.get_value(
			"Investment Type", self.investment_type, "is_active"
		):
			frappe.throw(_("Investment Type {0} is inactive").format(frappe.bold(self.investment_type)))

	def validate_institutions_are_active(self):
		if self.docstatus != 0:
			return

		for fieldname in ("issuer", "custodian"):
			institution = self.get(fieldname)
			if institution and not frappe.db.get_value("Financial Institution", institution, "is_active"):
				frappe.throw(_("Financial Institution {0} is inactive").format(frappe.bold(institution)))

	def validate_dates(self):
		if self.maturity_date and getdate(self.maturity_date) <= getdate(self.purchase_date):
			frappe.throw(_("Maturity Date must be after Purchase Date"))

	def validate_rate_type(self):
		if self.instrument_class not in ("Deposit", "Bond"):
			return

		if self.rate_type == "Floating":
			frappe.throw(_("Floating rate instruments are not supported yet. Please use Rate Type Fixed."))

	def validate_amounts(self):
		for fieldname in ("approved_amount", "principal_amount", "face_value"):
			if flt(self.get(fieldname)) < 0:
				frappe.throw(_("{0} cannot be negative").format(_(self.meta.get_label(fieldname))))

		if flt(self.approved_amount) <= 0:
			frappe.throw(_("Approved Amount must be greater than zero"))

	def validate_company_links(self):
		company_links = [(fieldname, "Account") for fieldname in ACCOUNT_FIELDS]
		company_links.append(("cost_center", "Cost Center"))

		for fieldname, doctype in company_links:
			value = self.get(fieldname)
			if value and frappe.get_cached_value(doctype, value, "company") != self.company:
				frappe.throw(
					_("{0} {1} does not belong to Company {2}").format(
						_(doctype), frappe.bold(value), frappe.bold(self.company)
					)
				)

	def set_status(self):
		if self.docstatus == 1:
			if self.status in ("Draft", *WORKFLOW_DRAFT_STATES):
				self.status = "Active"
			return

		workflow_state = self.get_workflow_state()
		self.status = workflow_state if workflow_state in WORKFLOW_DRAFT_STATES else "Draft"

	def get_workflow_state(self):
		workflow_name = get_workflow_name(self.doctype)
		if not workflow_name:
			return None

		return self.get(get_workflow_state_field(workflow_name))

	def update_position(self):
		"""Recompute lot balances and position totals from the ledger; called on transaction submit/cancel."""
		self.update_lot_balances()

		total_cost = self.get_ledger_balance(self.investment_account)
		values = {
			"total_cost": total_cost,
			"accrued_interest": self.get_ledger_balance(self.accrued_interest_account),
			"units_held": self.get_units_held(),
			"market_value": total_cost + flt(self.unrealised_gain_loss),
			"status": self.get_position_status(total_cost),
		}
		self.db_set(values)

	def update_lot_balances(self):
		from investment.investment.doctype.investment_transaction.investment_transaction import get_lots

		units_sold = self.get_exited_units()
		for lot in get_lots(self.name):
			consumed = min(flt(lot.units), units_sold)
			units_sold -= consumed

			if flt(lot.units_remaining) != flt(lot.units) - consumed:
				frappe.db.set_value("Investment Lot", lot.name, "units_remaining", flt(lot.units) - consumed)

	def get_exited_units(self):
		return flt(sum(t.units for t in self.get_exit_transactions()))

	def get_exit_transactions(self):
		from investment.investment.doctype.investment_transaction.investment_transaction import EXIT_TYPES

		return frappe.get_all(
			"Investment Transaction",
			filters={"investment_holding": self.name, "docstatus": 1, "transaction_type": ("in", EXIT_TYPES)},
			fields=["transaction_type", "units"],
		)

	def get_ledger_balance(self, account):
		if not account:
			return 0

		gl_entry = frappe.qb.DocType("GL Entry")
		transaction = frappe.qb.DocType("Investment Transaction")
		balance = (
			frappe.qb.from_(gl_entry)
			.join(transaction)
			.on(gl_entry.voucher_no == transaction.name)
			.select(Sum(gl_entry.debit) - Sum(gl_entry.credit))
			.where(gl_entry.voucher_type == "Investment Transaction")
			.where(gl_entry.account == account)
			.where(gl_entry.is_cancelled == 0)
			.where(transaction.investment_holding == self.name)
		).run()

		return flt(balance[0][0], self.precision("total_cost"))

	def get_units_held(self):
		lots = frappe.get_all(
			"Investment Lot", filters={"investment_holding": self.name}, pluck="units_remaining"
		)
		return flt(sum(flt(units) for units in lots))

	def get_position_status(self, total_cost):
		exit_types = {t.transaction_type for t in self.get_exit_transactions()}

		if not exit_types:
			return "Active"

		if total_cost > 0:
			return "Partially Redeemed"

		return "Matured" if "Maturity" in exit_types else "Redeemed"
