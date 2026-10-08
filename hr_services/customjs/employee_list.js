// Core Employee list colours only Active/Inactive/Suspended/Left; give the
// custom "Under Recruitment" status its own indicator.
(function () {
	frappe.listview_settings["Employee"] = frappe.listview_settings["Employee"] || {};
	const settings = frappe.listview_settings["Employee"];
	const core_get_indicator = settings.get_indicator;

	settings.get_indicator = function (doc) {
		if (doc.status === "Under Recruitment") {
			return [__(doc.status), "blue", "status,=," + doc.status];
		}
		return core_get_indicator && core_get_indicator(doc);
	};
})();
