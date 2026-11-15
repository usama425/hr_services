// HRMS v16 hides every Payroll Entry already referenced by a Journal Entry (including its
// own accrual JV), so the salary payment JV cannot be linked. Use our query for the
// "Payroll Entry" reference type only and keep the existing query for everything else.
frappe.ui.form.on("Journal Entry", {
	setup(frm) {
		const grid_field = frm.fields_dict.accounts.grid.get_field("reference_name");
		const previous_query = grid_field && grid_field.get_query;

		frm.set_query("reference_name", "accounts", function (doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			if (row.reference_type === "Payroll Entry") {
				return { query: "hr_services.custompy.payroll_entry.get_payroll_entries_for_jv" };
			}
			return previous_query ? previous_query(doc, cdt, cdn) : {};
		});
	},
});
