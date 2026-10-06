app_name = "investment"
app_title = "Investment"
app_publisher = "Aagnya Mistry"
app_description = "Investment management for ERPNext: record, value, and track financial investments such as time deposits, corporate bonds, and investment funds through their full lifecycle."
app_email = "aagnya.mistry@gmail.com"
app_license = "mit"

required_apps = ["erpnext"]

app_include_js = "/assets/investment/js/treasury.js"

# automatically load and sync documents of this doctype from downstream apps
importable_doctypes = ["Number Card", "Dashboard Chart"]

scheduler_events = {
	"daily": [
		"investment.treasury.doctype.investment_interest_accrual.investment_interest_accrual.make_draft_accruals"
	],
}
