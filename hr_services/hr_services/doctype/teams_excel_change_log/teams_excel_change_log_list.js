frappe.listview_settings["Teams Excel Change Log"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colors = { Sent: "green", Pending: "orange", Failed: "red" };
		return [__(doc.status), colors[doc.status] || "grey", `status,=,${doc.status}`];
	},
};
