// Copyright (c) 2023, Elite Resources and contributors
// For license information, please see license.txt

frappe.ui.form.on('Sales Invoice', {
	refresh: function(frm) {
		//setting the return series if is_return is checked
		if(frm.doc.is_return == 1){
			frm.set_value("naming_series","ACC-SINV-RET-.YYYY.-")
		}
		//merge selected attachments of the linked RFP into one PDF on this invoice
		if(!frm.is_new()){
			const rfps = get_linked_rfps(frm);
			if(rfps.length){
				frm.add_custom_button(__('Merge RFP Attachments'), function(){
					open_merge_attachments_dialog(frm, rfps);
				});
			}
		}
	},
	setup(frm) {
		frm.set_query("customer", function(){
            return{
                filters: [
                    ["Customer","is_standard_invoice_customer","=", 1],
                ]
            }
        });

		frm.set_query("print_customer", function(){
		    return {
		        filters: [
		            ["Customer","project_id","in", frm.doc.project],
                    ["Customer","is_standard_invoice_customer","=",0]
		        ]
		    }
		});

		frm.fields_dict['items'].grid.get_field("employee_id").get_query = function(doc, cdt, cdn) {
			return {
				filters: [
					['Employee', 'project', 'in',frm.doc.project],
				]
			}
        }
	},
	is_return(frm) {
		if(frm.doc.is_return == 1){
			frm.set_value("naming_series","ACC-SINV-RET-.YYYY.-")
		}
		else{
			frm.set_value("naming_series","Draft-.YYYY.-")
		}
	}
});

// Distinct Request For Payment(s) this invoice is linked to, via the item rows' custom_rfp.
function get_linked_rfps(frm){
	const seen = {};
	(frm.doc.items || []).forEach(function(row){
		if(row.custom_rfp){ seen[row.custom_rfp] = 1; }
	});
	return Object.keys(seen);
}

// Open a dialog listing the linked RFP's PDF/image attachments so the user can tick
// which ones to combine into a single merged PDF attached to this invoice.
function open_merge_attachments_dialog(frm, rfps){
	const mergeable = ['pdf','png','jpg','jpeg','gif','bmp','tiff','tif','webp'];
	const multi = rfps.length > 1;
	frappe.db.get_list('File', {
		filters: { attached_to_doctype: 'Request For Payment', attached_to_name: ['in', rfps] },
		fields: ['name','file_name','file_url','attached_to_name'],
		order_by: 'attached_to_name asc, creation asc',
		limit: 200,
	}).then(function(files){
		const items = (files || []).filter(function(f){
			const nm = (f.file_name || f.file_url || '').toLowerCase();
			return mergeable.indexOf(nm.split('.').pop()) !== -1;
		});
		if(!items.length){
			frappe.msgprint(__('The linked Request For Payment ({0}) has no PDF or image attachments.', [rfps.join(', ')]));
			return;
		}
		const rows = items.map(function(f){
			const prefix = multi ? ('<span class="text-muted">[' + frappe.utils.escape_html(f.attached_to_name) + ']</span> ') : '';
			const label = prefix + frappe.utils.escape_html(f.file_name || f.name);
			const key = frappe.utils.escape_html(f.name);
			return '<div class="checkbox" style="margin:4px 0;"><label>'
				+ '<input type="checkbox" class="merge-file" data-name="' + key + '" checked> '
				+ label + '</label></div>';
		}).join('');
		const html = '<div>'
			+ '<div style="margin-bottom:8px;"><label>'
			+ '<input type="checkbox" class="merge-select-all" checked> <b>' + __('Select all') + '</b>'
			+ '</label></div>'
			+ '<div class="merge-file-list" style="max-height:320px;overflow:auto;">' + rows + '</div>'
			+ '</div>';
		const d = new frappe.ui.Dialog({
			title: __('Merge RFP Attachments into one PDF'),
			fields: [{ fieldtype: 'HTML', fieldname: 'files_html', options: html }],
			primary_action_label: __('Merge'),
			primary_action: function(){
				const selected = [];
				d.$wrapper.find('.merge-file:checked').each(function(){
					selected.push($(this).attr('data-name'));
				});
				if(selected.length < 2){
					frappe.msgprint(__('Please select at least two attachments to merge.'));
					return;
				}
				frappe.call({
					method: 'hr_services.custompy.sales_invoice.merge_attachments_to_invoice',
					args: { invoice: frm.doc.name, file_names: JSON.stringify(selected) },
					freeze: true,
					freeze_message: __('Merging attachments…'),
					callback: function(r){
						if(!r.message){ return; }
						d.hide();
						let msg = __('Merged {0} attachment(s) into {1}.', [r.message.merged, r.message.file_name]);
						if(r.message.skipped && r.message.skipped.length){
							const sk = r.message.skipped.map(function(s){
								return Array.isArray(s) ? (s[0] + ' — ' + s[1]) : s;
							}).join('<br>');
							msg += '<br><br>' + __('Skipped:') + '<br>' + sk;
						}
						frappe.msgprint({ title: __('Done'), message: msg, indicator: 'green' });
						frm.reload_doc();
						if(r.message.file_url){ window.open(r.message.file_url, '_blank'); }
					}
				});
			}
		});
		d.$wrapper.on('change', '.merge-select-all', function(){
			d.$wrapper.find('.merge-file').prop('checked', $(this).prop('checked'));
		});
		d.$wrapper.on('change', '.merge-file', function(){
			const all = d.$wrapper.find('.merge-file').length;
			const checked = d.$wrapper.find('.merge-file:checked').length;
			d.$wrapper.find('.merge-select-all').prop('checked', all === checked);
		});
		d.show();
	});
}