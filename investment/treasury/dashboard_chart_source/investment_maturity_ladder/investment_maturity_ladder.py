# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import get_first_day, nowdate
from frappe.utils.dashboard import cache_source

from investment.treasury.dashboard import HELD_STATUSES, get_company, get_month_ends, get_monthly_chart


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
	"""Book value of held investments maturing in each of the next 12 months."""
	month_ends = get_month_ends(nowdate(), 12)
	investments = frappe.get_list(
		"Investment",
		filters={
			"company": get_company(filters),
			"docstatus": 1,
			"status": ("in", HELD_STATUSES),
			"maturity_date": ("between", [get_first_day(nowdate()), month_ends[-1]]),
		},
		fields=["maturity_date", "total_cost"],
		as_list=True,
	)

	return get_monthly_chart(month_ends, investments, _("Book Value Maturing"), "bar")
