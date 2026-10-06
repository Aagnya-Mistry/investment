// Copyright (c) 2026, Aagnya Mistry and contributors
// For license information, please see license.txt

frappe.query_reports["Upcoming Maturities"] = {
	filters: [
		investment.treasury.get_company_filter(),
		investment.treasury.get_as_on_date_filter(),
		...investment.treasury.get_investment_filters(),
	],
	formatter: investment.treasury.report_formatter,
};
