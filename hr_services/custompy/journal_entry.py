import frappe

REFERENCE_FIELDS = ("reference_type", "reference_name")


def repost_if_references_changed(doc, method=None):
	"""Finance can change Reference Type / Reference Name on a submitted Journal Entry
	(allow on submit). Repost its ledger entries so GL and Payment Ledger match the new
	references. ERPNext only reposts by itself for cost center, project and dimensions."""
	if doc.flags.get("ignore_reposting_on_reconciliation"):
		return

	before = doc.get_doc_before_save()
	if not before:
		return

	old_rows = {row.name: row for row in before.get("accounts")}
	changed = any(
		old_rows.get(row.name) is None
		or any(old_rows[row.name].get(f) != row.get(f) for f in REFERENCE_FIELDS)
		for row in doc.get("accounts")
	)
	if not changed:
		return

	# ERPNext's own on_update_after_submit already reposted if dimensions changed too
	if doc.check_if_fields_updated(fields_to_check=[], child_tables={"accounts": []}):
		return

	doc.validate_for_repost()
	doc.repost_accounting_entries()
