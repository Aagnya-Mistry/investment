# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name, get_workflow_state_field
from frappe.utils import flt, getdate

WORKFLOW_DRAFT_STATES = ("Pending Approval", "Rejected")


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
		for fieldname, doctype in (
			("investment_account", "Account"),
			("interest_income_account", "Account"),
			("cost_center", "Cost Center"),
		):
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
