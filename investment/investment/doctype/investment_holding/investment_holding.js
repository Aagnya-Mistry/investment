// Copyright (c) 2026, Aagnya Mistry and contributors
// For license information, please see license.txt

frappe.ui.form.on("Investment Holding", {
	setup(frm) {
		frm.set_query("investment_type", () => ({ filters: { is_active: 1 } }));
		frm.set_query("issuer", () => ({ filters: { is_active: 1 } }));
		frm.set_query("custodian", () => ({ filters: { is_active: 1 } }));

		for (const fieldname of [
			"investment_account",
			"accrued_interest_account",
			"interest_income_account",
			"dividend_income_account",
			"realised_gain_loss_account",
			"unrealised_gain_loss_account",
			"fair_value_adjustment_account",
			"tax_withheld_receivable_account",
			"charges_account",
			"impairment_loss_account",
			"impairment_provision_account",
		]) {
			frm.set_query(fieldname, () => ({
				filters: { company: frm.doc.company, is_group: 0 },
			}));
		}

		frm.set_query("cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
	},
});

frappe.ui.form.on("Investment Holding", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && ["Deposit", "Bond"].includes(frm.doc.instrument_class)) {
			frm.add_custom_button(__("Accrue Interest"), () => accrue_interest(frm));
		}

		if (
			frm.doc.docstatus === 1 &&
			["Partially Redeemed", "Matured", "Redeemed"].includes(frm.doc.status)
		) {
			frm.add_custom_button(__("Roll Over"), () =>
				frappe.new_doc("Investment Rollover", { original_holding: frm.doc.name })
			);
		}
	},
});

function accrue_interest(frm) {
	frappe.prompt(
		{
			fieldname: "upto",
			fieldtype: "Date",
			label: __("Accrue Interest Upto"),
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		(values) => {
			frappe.call({
				method: "investment.investment.doctype.investment_interest_accrual.investment_interest_accrual.accrue_interest_upto",
				args: { investment_holding: frm.doc.name, upto: values.upto },
				freeze: true,
				callback(r) {
					const count = (r.message || []).length;
					frappe.show_alert({
						message: count
							? __("{0} Interest Accrual transaction(s) posted", [count])
							: __("No interest left to accrue up to this date"),
						indicator: count ? "green" : "blue",
					});
					frm.reload_doc();
				},
			});
		},
		__("Accrue Interest")
	);
}
