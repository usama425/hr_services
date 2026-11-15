// HRMS v16 hides every Payroll Entry already referenced by a Journal Entry (including its
// own accrual JV), so the salary payment JV cannot be linked. Use our query for the
// "Payroll Entry" reference type only and keep the existing query for everything else.
// HRMS (re)sets its query in `refresh`, so ours is applied there too; it runs after HRMS.
const hr_services_set_payroll_entry_query = (frm) => {
	const grid_field = frm.fields_dict.accounts.grid.get_field("reference_name");
	const previous_query = grid_field && grid_field.get_query;
	if (previous_query && previous_query.hr_services_payroll_query) return;

	const query = function (doc, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.reference_type === "Payroll Entry") {
			// credit row = accrual side (any submitted payroll); debit row = payment (unpaid only)
			const entry_side = flt(row.credit_in_account_currency) > 0 ? "credit" : "debit";
			return {
				query: "hr_services.custompy.payroll_entry.get_payroll_entries_for_jv",
				filters: { entry_side: entry_side },
			};
		}
		return previous_query ? previous_query(doc, cdt, cdn) : {};
	};
	query.hr_services_payroll_query = true;
	frm.set_query("reference_name", "accounts", query);
};

frappe.ui.form.on("Journal Entry", {
	onload(frm) {
		hr_services_set_payroll_entry_query(frm);
	},
	refresh(frm) {
		hr_services_set_payroll_entry_query(frm);
	},
});
