# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, cstr, date_diff, flt, getdate

from investment.investment.doctype.investment_transaction.investment_transaction import EXIT_TYPES
from investment.investment.interest import INTEREST_CLASSES

ROLLOVER_TYPES = {"Deposit": "Auto Renewal", "Units": "Switch Scheme", "Bond": "Reinvestment"}

# holding fields that make up the "terms" of an instrument, compared to set New Terms Changed
TERM_FIELDS = {
	"Deposit": ("rate_of_interest", "interest_payout_type", "compounding_frequency", "payout_frequency"),
	"Bond": ("coupon_rate", "coupon_frequency"),
	"Units": ("scheme_name",),
}


class InvestmentRollover(Document):
	def validate(self):
		self.validate_original_holding()
		self.set_rollover_type()
		self.validate_dates()
		self.validate_amounts()

	def before_submit(self):
		self.validate_available_proceeds()

	def on_submit(self):
		self.create_new_holding()

	def on_cancel(self):
		self.delete_draft_new_holding()

	def get_original_holding(self):
		if not getattr(self, "_original_holding", None):
			self._original_holding = frappe.get_doc("Investment Holding", self.original_holding)

		return self._original_holding

	def validate_original_holding(self):
		if self.get_original_holding().docstatus != 1:
			frappe.throw(
				_("Original Holding {0} must be approved (submitted) before it can be rolled over").format(
					frappe.bold(self.original_holding)
				)
			)

	def set_rollover_type(self):
		self.rollover_type = ROLLOVER_TYPES.get(self.instrument_class)

		if self.instrument_class not in INTEREST_CLASSES:
			self.interest_rolled_over = 0

	def validate_dates(self):
		if getdate(self.rollover_date) < getdate(self.get_original_holding().purchase_date):
			frappe.throw(_("Rollover Date cannot be before the Original Holding's Purchase Date"))

	def validate_amounts(self):
		if flt(self.principal_rolled_over) <= 0:
			frappe.throw(_("Principal Rolled Over must be greater than zero"))

		if flt(self.interest_rolled_over) < 0:
			frappe.throw(_("Interest Rolled Over cannot be negative"))

		self.total_rolled_over = flt(
			flt(self.principal_rolled_over) + flt(self.interest_rolled_over),
			self.precision("total_rolled_over"),
		)

	def validate_available_proceeds(self):
		"""Only money the original holding actually paid out (and not already rolled over) can be rolled over."""
		for fieldname, transaction_types in (
			("principal_rolled_over", EXIT_TYPES),
			("interest_rolled_over", ("Interest Receipt",)),
		):
			available = self.get_paid_out(transaction_types) - self.get_already_rolled_over(fieldname)
			if flt(self.get(fieldname)) > flt(available, self.precision(fieldname)):
				frappe.throw(
					_(
						"{0} cannot be more than {1}, the amount paid out by {2} up to {3} and not yet rolled over"
					).format(
						_(self.meta.get_label(fieldname)),
						frappe.bold(frappe.format_value(max(available, 0), currency=self.currency)),
						frappe.bold(self.original_holding),
						frappe.bold(frappe.format_value(self.rollover_date, "Date")),
					)
				)

	def get_paid_out(self, transaction_types):
		transaction = frappe.qb.DocType("Investment Transaction")
		paid_out = (
			frappe.qb.from_(transaction)
			.select(Sum(transaction.net_amount))
			.where(transaction.investment_holding == self.original_holding)
			.where(transaction.docstatus == 1)
			.where(transaction.transaction_type.isin(transaction_types))
			.where(transaction.posting_date <= self.rollover_date)
		).run()

		return flt(paid_out[0][0])

	def get_already_rolled_over(self, fieldname):
		rollover = frappe.qb.DocType("Investment Rollover")
		rolled_over = (
			frappe.qb.from_(rollover)
			.select(Sum(rollover[fieldname]))
			.where(rollover.original_holding == self.original_holding)
			.where(rollover.docstatus == 1)
			.where(rollover.name != self.name)
		).run()

		return flt(rolled_over[0][0])

	def create_new_holding(self):
		new_holding = frappe.copy_doc(self.get_original_holding())
		new_holding.update(self.get_new_holding_values())
		new_holding.insert()

		self.db_set("new_holding", new_holding.name)
		frappe.msgprint(
			_(
				"Draft Investment Holding {0} created. Submit it for approval to complete the rollover."
			).format(frappe.get_desk_link("Investment Holding", new_holding.name)),
			alert=True,
		)

	def get_new_holding_values(self):
		values = {
			"docstatus": 0,
			"purchase_date": self.rollover_date,
			"maturity_date": self.get_new_maturity_date(),
			"approved_amount": self.total_rolled_over,
			"investment_rationale": _(
				"Rolled over from Investment Holding {0} via Investment Rollover {1}"
			).format(self.original_holding, self.name),
			"instrument_id": None,
			"approval_reference": None,
		}

		if self.instrument_class == "Deposit":
			values["principal_amount"] = self.total_rolled_over

		return values

	def get_new_maturity_date(self):
		"""Keep the original tenure, counted from the rollover date."""
		original = self.get_original_holding()
		if not original.maturity_date:
			return None

		return add_days(self.rollover_date, date_diff(original.maturity_date, original.purchase_date))

	def delete_draft_new_holding(self):
		"""A draft new holding only exists because of this rollover; an approved one is left as it is."""
		if not self.new_holding or frappe.db.get_value("Investment Holding", self.new_holding, "docstatus"):
			return

		new_holding = self.new_holding
		self.db_set("new_holding", None)
		frappe.delete_doc("Investment Holding", new_holding)


def update_new_terms_changed(new_holding):
	"""Called when a holding is saved: flag the rollover that created it if its terms were edited."""
	rollover = frappe.db.get_value(
		"Investment Rollover",
		{"new_holding": new_holding.name, "docstatus": 1},
		["name", "original_holding"],
		as_dict=True,
	)
	if not rollover:
		return

	original = frappe.get_doc("Investment Holding", rollover.original_holding)
	frappe.db.set_value(
		"Investment Rollover", rollover.name, "new_terms_changed", has_new_terms(original, new_holding)
	)


def has_new_terms(original, new_holding):
	fieldnames = TERM_FIELDS.get(original.instrument_class, ())
	if any(cstr(original.get(f)) != cstr(new_holding.get(f)) for f in fieldnames):
		return 1

	return int(get_tenure(original) != get_tenure(new_holding))


def get_tenure(holding):
	if not holding.maturity_date:
		return None

	return date_diff(holding.maturity_date, holding.purchase_date)
