// Copyright (c) 2026, Aagnya Mistry and contributors
// For license information, please see license.txt

frappe.listview_settings["Investment Interest Accrual"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const color = doc.status === "Active" ? "green" : "red";
		return [__(doc.status), color, `status,=,${doc.status}`];
	},
};
