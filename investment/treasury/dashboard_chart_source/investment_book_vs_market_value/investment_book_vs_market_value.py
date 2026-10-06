# Copyright (c) 2026, Aagnya Mistry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.dashboard import cache_source

from investment.treasury.dashboard import HELD_STATUSES, get_company


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
	"""Book value next to market value (at the last revaluation) of held investments, per investment type."""
	types = frappe.get_list(
		"Investment",
		filters={"company": get_company(filters), "docstatus": 1, "status": ("in", HELD_STATUSES)},
		fields=[
			"investment_type",
			{"SUM": "total_cost", "as": "book_value"},
			{"SUM": "market_value", "as": "market_value"},
		],
		group_by="investment_type",
		order_by="book_value desc",
	)

	return {
		"labels": [row.investment_type for row in types],
		"datasets": [
			{"name": _("Book Value"), "values": [row.book_value for row in types]},
			{"name": _("Market Value"), "values": [row.market_value for row in types]},
		],
		"type": "bar",
	}
