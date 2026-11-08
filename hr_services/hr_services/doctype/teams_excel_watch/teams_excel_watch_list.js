frappe.listview_settings["Teams Excel Watch"] = {
	add_fields: ["status", "enabled"],
	get_indicator(doc) {
		if (!doc.enabled) return [__("Disabled"), "grey", "enabled,=,0"];
		const colors = { New: "blue", Watching: "green", "Changes Pending": "orange", Error: "red" };
		return [__(doc.status), colors[doc.status] || "grey", `status,=,${doc.status}`];
	},
};
