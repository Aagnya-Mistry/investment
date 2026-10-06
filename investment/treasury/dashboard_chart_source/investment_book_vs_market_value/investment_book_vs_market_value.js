frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Investment Book vs Market Value"] = {
	method: "investment.treasury.dashboard_chart_source.investment_book_vs_market_value.investment_book_vs_market_value.get",
	filters: [investment.treasury.get_company_filter()],
};
