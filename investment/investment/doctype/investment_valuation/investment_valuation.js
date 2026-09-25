// Copyright (c) 2026, Aagnya Mistry and contributors
// For license information, please see license.txt

const VALUATION_INSTRUMENTS = {
	"Market Valuation": ["Units", "Bond"],
	"Impairment Assessment": ["Deposit", "Bond"],
};

frappe.ui.form.on("Investment Valuation", {
	setup(frm) {
		frm.set_query("cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));

		frm.set_query("investment_holding", "holdings", () => ({
			filters: {
				company: frm.doc.company,
				docstatus: 1,
				instrument_class: ["in", VALUATION_INSTRUMENTS[frm.doc.valuation_type] || []],
			},
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Get Holdings"), () => get_holdings(frm));
		}
	},

	company(frm) {
		frm.clear_table("holdings");
		frm.refresh_field("holdings");
	},

	valuation_type(frm) {
		frm.clear_table("holdings");
		frm.refresh_field("holdings");
	},
});

function get_holdings(frm) {
	if (!frm.doc.company || !frm.doc.valuation_date) {
		frappe.msgprint(__("Please set Company and Valuation Date first"));
		return;
	}

	frm.call({
		doc: frm.doc,
		method: "set_holdings",
		freeze: true,
		callback() {
			frm.dirty();
			frm.refresh_field("holdings");
			if (!frm.doc.holdings.length) {
				frappe.show_alert({ message: __("No holdings to value"), indicator: "blue" });
			}
		},
	});
}
