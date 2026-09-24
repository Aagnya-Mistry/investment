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
