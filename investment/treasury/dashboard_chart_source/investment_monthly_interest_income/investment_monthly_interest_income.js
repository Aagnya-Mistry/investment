frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Investment Monthly Interest Income"] = {
	method: "investment.treasury.dashboard_chart_source.investment_monthly_interest_income.investment_monthly_interest_income.get",
	filters: [investment.treasury.get_company_filter()],
};
