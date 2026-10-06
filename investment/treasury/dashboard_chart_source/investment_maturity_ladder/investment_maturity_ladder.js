frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Investment Maturity Ladder"] = {
	method: "investment.treasury.dashboard_chart_source.investment_maturity_ladder.investment_maturity_ladder.get",
	filters: [investment.treasury.get_company_filter()],
};
