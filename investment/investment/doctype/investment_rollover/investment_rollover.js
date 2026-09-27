// Copyright (c) 2026, Aagnya Mistry and contributors
// For license information, please see license.txt

frappe.ui.form.on("Investment Rollover", {
	setup(frm) {
		frm.set_query("original_holding", () => ({
			filters: {
				docstatus: 1,
				status: ["in", ["Partially Redeemed", "Matured", "Redeemed"]],
			},
		}));
	},

	principal_rolled_over(frm) {
		set_total_rolled_over(frm);
	},

	interest_rolled_over(frm) {
		set_total_rolled_over(frm);
	},
});

function set_total_rolled_over(frm) {
	frm.set_value(
		"total_rolled_over",
		flt(frm.doc.principal_rolled_over) + flt(frm.doc.interest_rolled_over)
	);
}
