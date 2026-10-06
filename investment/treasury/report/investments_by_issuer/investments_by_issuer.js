// Copyright (c) 2026, Aagnya Mistry and contributors
// For license information, please see license.txt

frappe.query_reports["Investments by Issuer"] = {
	filters: [
		investment.treasury.get_company_filter(),
		investment.treasury.get_as_on_date_filter(),
		investment.treasury.get_investment_filters()[0],
	],
	formatter: investment.treasury.report_formatter,
};
