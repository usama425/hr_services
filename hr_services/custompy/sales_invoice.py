# Copyright (c) 2023, Elite Resources and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import get_url_to_form
from frappe.utils import flt, get_first_day, get_last_day, getdate, nowdate
from frappe import _

def copy_draft(draft):
	# Copy a Draft- series invoice into the ACC-SINV series
	new_doc = frappe.copy_doc(draft)
	new_doc.naming_series = 'ACC-SINV-.YYYY.-'
	# posting_date/time are no_copy, so carry over the date chosen on the draft
	new_doc.set_posting_time = 1
	new_doc.posting_date = draft.posting_date
	new_doc.posting_time = draft.posting_time
	return new_doc

def new_rec(doc, method):
	# on_submit: issue the ACC-SINV invoice for a submitted Draft- invoice. The
	# draft itself is removed later, in remove_submitted_draft (on_change):
	# zatca_erpgulf's on_submit hook runs after this one and reloads the draft,
	# so deleting it here failed with "Sales Invoice Draft-... not found".
	if doc.docstatus == 1 and "Draft" in doc.name and not doc.flags.issued_invoice:
		# Create a new Sales Invoice with a different naming series
		new_doc = copy_draft(doc)

		# Save the new document
		new_doc.insert(ignore_permissions=True)
		new_doc.submit()

		# Copy attachments from the original draft to the new invoice
		copy_attachments(doc, new_doc)

		doc.flags.issued_invoice = new_doc.name

def remove_submitted_draft(doc, method):
	# on_change also fires from db_set calls inside on_submit hooks (ERPNext's
	# KSA QR code), i.e. before zatca_erpgulf's hook. The flag is only set once
	# new_rec has run, and is cleared so the draft is removed exactly once.
	issued_invoice = doc.flags.issued_invoice
	if not issued_invoice or doc.flags.draft_removed:
		return
	doc.flags.draft_removed = True

	# Cancel the existing draft
	frappe.get_doc('Sales Invoice', doc.name).cancel()

	# Delete the existing draft
	frappe.delete_doc('Sales Invoice', doc.name, ignore_permissions=True)

	# Open the new Sales Invoice in the same tab
	url = get_url_to_form("Sales Invoice", issued_invoice)
	# Construct a message with a clickable link
	message = f"Invoice Issued: <a href='{url}'>{issued_invoice}</a>"

	# Display the message with a clickable link
	frappe.msgprint(message, indicator='green')

@frappe.whitelist()
def submit_draft_invoice(invoice_name):
	"""Actions > Submit without ZATCA. Replaces the "Submit Draft Invoice" Server
	Script (via override_whitelisted_methods), which had no locking: a double
	click created two submitted ACC-SINV invoices from one draft.

	Locking the draft row makes a second request wait for the first, then find
	the draft gone instead of copying it again.
	"""
	if not frappe.db.sql("select name from `tabSales Invoice` where name=%s for update", invoice_name):
		frappe.throw(_("Sales Invoice {0} has already been submitted. Open the Sales Invoice list to find its ACC-SINV invoice.").format(invoice_name))

	source_invoice = frappe.get_doc("Sales Invoice", invoice_name)
	if source_invoice.docstatus != 0:
		frappe.throw(_("Only draft invoices can be submitted"))

	new_invoice = copy_draft(source_invoice)
	new_invoice.amended_from = None

	# Add default VAT tax if no taxes exist
	if not source_invoice.taxes:
		new_invoice.append("taxes", {
			"charge_type": "On Net Total",
			"account_head": "VAT 15% - ERC",
			"description": "VAT 15%",
			"rate": 15,
		})

	new_invoice.insert(ignore_permissions=True)
	new_invoice.submit()

	copy_attachments(source_invoice, new_invoice)

	frappe.delete_doc("Sales Invoice", invoice_name, ignore_permissions=True)

	return new_invoice.name

def allow_posting_date_edit(doc, method):
	# Posting date is editable on drafts, so keep whatever date was entered
	# instead of letting ERPNext reset it to today on save or submit.
	if doc.docstatus == 0 or doc.get("_action") == "submit":
		doc.set_posting_time = 1

		# A due date before the posting date fails ERPNext's validation
		# ("Due Date cannot be before Posting / Supplier Invoice Date"), so pull
		# it forward to the posting date, i.e. due on the invoice date.
		posting_date = getdate(doc.posting_date)
		if doc.due_date and getdate(doc.due_date) < posting_date:
			doc.due_date = posting_date
		for row in doc.get("payment_schedule") or []:
			if row.due_date and getdate(row.due_date) < posting_date:
				row.due_date = posting_date

def copy_attachments(source_doc, target_doc):
	# Copy attachments from the source document to the target document
	for attachment in frappe.get_all('File', filters={'attached_to_doctype': source_doc.doctype, 'attached_to_name': source_doc.name}):
		file_doc = frappe.get_doc('File', attachment.name)
		file_copy = frappe.copy_doc(file_doc, ignore_no_copy=False)
		file_copy.attached_to_doctype = target_doc.doctype
		file_copy.attached_to_name = target_doc.name
		file_copy.insert()


@frappe.whitelist()
def merge_attachments_to_invoice(invoice, file_names, include_print=1, print_format=None):
	"""Merge selected attachments (PDFs and/or images) of the Request For Payment(s)
	linked to this Sales Invoice into a single PDF, and attach it to the invoice.

	When include_print is set, the Sales Invoice print format is rendered and placed as
	the first page(s), on top of the RFP attachments. print_format selects which format
	to use; when blank it falls back to the default (the one the Print button uses). The
	source files live on the linked RFP (resolved via the invoice item
	rows' custom_rfp); the merged PDF is attached back to the Sales Invoice.
	Non-destructive: the individual RFP attachments are kept. Only a previously
	auto-generated merged file (matched by prefix) is replaced, so re-running updates
	rather than duplicates.
	"""
	import json
	from io import BytesIO
	from PyPDF2 import PdfReader, PdfWriter
	from PIL import Image
	from frappe.utils import cint
	from frappe.utils.pdf import get_file_data_from_writer
	from frappe.utils.file_manager import save_file

	if not frappe.has_permission("Sales Invoice", "write", doc=invoice):
		frappe.throw(_("Not permitted to modify Sales Invoice {0}").format(invoice), frappe.PermissionError)

	if isinstance(file_names, str):
		file_names = json.loads(file_names)
	if not file_names:
		frappe.throw(_("No attachments selected."))

	# Source files come from the Request For Payment(s) linked to this invoice,
	# resolved via the item rows' custom_rfp. The merged PDF is attached to the invoice.
	linked_rfps = set(frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": invoice, "custom_rfp": ["is", "set"]},
		pluck="custom_rfp",
	))
	if not linked_rfps:
		frappe.throw(_("Sales Invoice {0} is not linked to any Request For Payment.").format(invoice))

	image_ext = ("png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif", "webp")
	writer = PdfWriter()
	merged_count = 0
	skipped = []

	# Put the Sales Invoice print format on top. print_format lets the caller choose
	# which format; when blank/invalid it falls back to the default (the Print button's).
	print_added = False
	if cint(include_print):
		pf = print_format or None
		if pf and not frappe.db.exists("Print Format", {"name": pf, "doc_type": "Sales Invoice"}):
			pf = None
		try:
			invoice_pdf = frappe.get_print("Sales Invoice", invoice, print_format=pf, as_pdf=True)
			writer.append_pages_from_reader(PdfReader(BytesIO(invoice_pdf)))
			print_added = True
		except Exception as e:
			skipped.append([_("Sales Invoice print format"), str(e)])

	for name in file_names:
		f = frappe.get_doc("File", name)
		# Only merge files that belong to the linked Request For Payment(s).
		if not (f.attached_to_doctype == "Request For Payment" and f.attached_to_name in linked_rfps):
			skipped.append([f.file_name or name, _("not an attachment of the linked Request For Payment")])
			continue
		ext = (f.file_name or f.file_url or "").rsplit(".", 1)[-1].lower()
		try:
			content = f.get_content()
			if isinstance(content, str):
				# A binary file that got decoded to str; re-read the raw bytes from disk.
				with open(f.get_full_path(), "rb") as fh:
					content = fh.read()
			if ext == "pdf":
				reader = PdfReader(BytesIO(content))
				if reader.is_encrypted:
					reader.decrypt("")
				writer.append_pages_from_reader(reader)
				merged_count += 1
			elif ext in image_ext:
				buf = BytesIO()
				Image.open(BytesIO(content)).convert("RGB").save(buf, format="PDF")
				writer.append_pages_from_reader(PdfReader(BytesIO(buf.getvalue())))
				merged_count += 1
			else:
				skipped.append([f.file_name or name, _("unsupported type")])
		except Exception as e:
			skipped.append([f.file_name or name, str(e)])

	min_attachments = 1 if print_added else 2
	if merged_count < min_attachments:
		frappe.throw(_("Select at least {0} PDF or image attachment(s) to merge (merged {1}).").format(min_attachments, merged_count))

	merged_bytes = get_file_data_from_writer(writer)

	# Replace any prior auto-generated merged file so repeated merges do not accumulate.
	# Match by prefix: Frappe may append a content-hash suffix before the extension
	# (e.g. "<base>-merged<hash>.pdf") when a same-named file already exists.
	merged_prefix = "{0}-merged".format(invoice.replace("/", "-"))
	merged_name = merged_prefix + ".pdf"
	for old in frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Sales Invoice",
			"attached_to_name": invoice,
			"file_name": ["like", merged_prefix + "%"],
		},
		pluck="name",
	):
		frappe.delete_doc("File", old, ignore_permissions=True)

	new_file = save_file(merged_name, merged_bytes, "Sales Invoice", invoice, is_private=1)

	return {
		"file_url": new_file.file_url,
		"file_name": new_file.file_name,
		"merged": merged_count,
		"print_included": print_added,
		"skipped": skipped,
	}


#get outstanding total from sales invoice for number card
@frappe.whitelist()
def get_customers_outstanding():
	# Define the date range for the current month
	first_day = get_first_day(nowdate())
	last_day = get_last_day(nowdate())

	# Query to get the outstanding amounts for sales invoices with docstatus = 1
	outstanding_amounts = frappe.db.sql("""
		SELECT SUM(outstanding_amount)
		FROM `tabSales Invoice`
		WHERE docstatus = 1
		AND posting_date BETWEEN %s AND %s
	""", (first_day, last_day))

	# Extract the sum from the query result
	outstanding_sum = flt(outstanding_amounts[0][0]) if outstanding_amounts else 0.0

	# Query to get the grand total amounts for sales invoices with docstatus = 1 and is_return = 1
	return_amounts = frappe.db.sql("""
		SELECT SUM(grand_total)
		FROM `tabSales Invoice`
		WHERE docstatus = 1 AND is_return = 1
		AND posting_date BETWEEN %s AND %s
	""", (first_day, last_day))

	# Extract the sum from the query result
	return_sum = flt(return_amounts[0][0]) if return_amounts else 0.0

	return {
		"value": outstanding_sum + return_sum,
		"fieldtype": "Currency",
		"route_options": {"docstatus": "1","posting_date":["Timespan","this month"],"outstanding_amount":[">",0]},
		"route": ["sales-invoice"]
	}

#function for check the return only invoice amount
def check_outstanding(doc, method):
	if doc.is_return == 1 and doc.return_against:
		sales_inv = frappe.get_doc("Sales Invoice",doc.return_against)
		return_total = -(doc.grand_total)

		if return_total > sales_inv.outstanding_amount:
			frappe.throw(
			_(
				"""The outstanding amount of Sales Invoice <b>{}</b> is <b>{}</b> <br> The return invoice amount is greater than outstanding amount"""
			).format(sales_inv.name, sales_inv.outstanding_amount, return_total),
			title=_("Error")
		)