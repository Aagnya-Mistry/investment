# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_months, get_first_day, nowdate
from frappe.utils.dashboard import cache_source

from investment.treasury.dashboard import get_company, get_income_query, get_month_ends, get_monthly_chart


@frappe.whitelist()
@cache_source
def get(
	chart_name=None,
	chart=None,
	no_cache=None,
	filters=None,
	from_date=None,
	to_date=None,
	timespan=None,
	time_interval=None,
	heatmap_year=None,
):
	"""Interest income, including bond amortisation, booked in each of the last 12 months."""
	frappe.has_permission("Investment Transaction", throw=True)
	month_ends = get_month_ends(add_months(nowdate(), -11), 12)
	gl_entry = frappe.qb.DocType("GL Entry")
	query = get_income_query(
		get_company(filters), "interest_income_account", get_first_day(month_ends[0]), month_ends[-1]
	)
	rows = query.select(gl_entry.posting_date).groupby(gl_entry.posting_date).run()

	return get_monthly_chart(
		month_ends, [(date, income) for income, date in rows], _("Interest Income"), "line"
	)
