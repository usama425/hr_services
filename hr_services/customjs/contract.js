// Copyright (c) 2023, Elite Resources and contributors
// For license information, please see license.txt

frappe.ui.form.on('Contract',{
    onload(frm){
        if(frm.doc.custom_posting_date && cur_frm.doc.__islocal){
            convert_into_hijri(frm);
        }
    },
    refresh(frm){
        calculate_end_date(frm);
        if(frm.doc.docstatus == 1){
            frm.add_custom_button(__('Create Word File'), function (){
                var dialog = new frappe.ui.Dialog({
                    title: __('Choose a template file'),
                    fields: [
                        {
                            fieldname: 'template',
                            fieldtype: 'Link',
                            label: 'Word Template',
                            options: 'Word Template'
                        },
                    ],
                    primary_action: () => {
                        dialog.hide();
                        frappe.call({
                            method: "hr_services.custompy.contract.fill_and_attach_template",
                            args: {
                                doctype: frm.doc.doctype,
                                name: frm.doc.name,
                                template: dialog.get_values().template,
                            },
                            freeze: true,
                            freeze_message: __("Creating Contract Word File......."),
                            callback: (r) => frm.reload_doc(),
                        });
                    }
                });
                dialog.show();
            }).addClass('btn-primary');
        }
        if(frm.doc.is_signed == 1 && frm.doc.docstatus == 1){
            add_employee_button(frm);
        }
    },
    custom_posting_date(frm){
        convert_into_hijri(frm);
    },
    // Picking the Job Offer fills the contract from it (candidate, project, customer,
    // visa, ID and salary). Everything stays editable afterwards.
    custom_job_offer(frm){
        if (!frm.doc.custom_job_offer || frm.doc.docstatus !== 0) return;
        frappe.call({
            method: "hr_services.custompy.job_offer.contract_values_from_offer",
            args: { job_offer: frm.doc.custom_job_offer },
            freeze: true,
            callback: async (r) => {
                const values = r.message || {};
                const salary = values.salary_detail || [];
                delete values.salary_detail;
                // party_type first, so party_name links to the right doctype
                if (values.party_type) await frm.set_value("party_type", values.party_type);
                delete values.party_type;
                await frm.set_value(values);
                if (salary.length) {
                    frm.clear_table("salary_detail");
                    salary.forEach((row) => frm.add_child("salary_detail", row));
                    frm.refresh_field("salary_detail");
                }
            },
        });
    },
    is_signed(frm){
        if(frm.doc.is_signed == 1){
            frm.set_value("custom_status", "Signed");
        }
        else if(frm.doc.is_signed == 0){
            frm.set_value("custom_status", "Sent");
        }
    },
    custom_contract_duration(frm){
        calculate_end_date(frm);
    },
    before_save(frm){
		if(frm.doc.salary_detail){
			calculate_salary(frm);
		}
	},
});

function calculate_end_date(frm){
    if(frm.doc.custom_contract_duration == 1){
        let end_date = frappe.datetime.add_days(frm.doc.start_date, 365);
        frm.set_value("end_date", end_date);
    }
    else if(frm.doc.custom_contract_duration == 2){
        let end_date = frappe.datetime.add_days(frm.doc.start_date, 730);
        frm.set_value("end_date", end_date);
    }
}

frappe.ui.form.on("Contract Details", {
	amount:function(frm, cdt, cdn){
		calculate_salary(frm);
	},
	salary_detail_remove(frm,cdt,cdn){
		calculate_salary(frm);
	}
});

function calculate_salary(frm){
    let total = 0;
	for(let i in frm.doc.salary_detail){
		total += frm.doc.salary_detail[i].amount;
	}
	frm.set_value("custom_total_salary", total);
	frm.refresh();
}

function convert_into_hijri(frm){
    frappe.call({
        method: "hr_services.custompy.employee.convert_into_hijri",
        args: {
            date: frm.doc.custom_posting_date
        },
        callback: function(r){
            if(r.message){
                frm.set_value("custom_date_in_hijri",r.message);
            }
        }
    });
}
// Offers in the recruitment workflow get their employee created automatically when the
// contract is signed, so only old (Legacy) offers keep the manual Create Employee button.
function add_employee_button(frm) {
    const offer = frm.doc.custom_job_offer;
    const manual = () => frm.add_custom_button(__('Create Employee'), function(){
        frappe.model.open_mapped_doc({
            method: "hr_services.custompy.contract.make_employee",
            frm: frm
        });
    }).addClass('btn-primary');
    if (!offer) return manual();
    frappe.db.get_value('Job Offer', offer, ['workflow_state', 'custom_employee']).then((r) => {
        const o = r.message || {};
        if (o.custom_employee) {
            frm.add_custom_button(__('Show Employee'), () => frappe.set_route('Form', 'Employee', o.custom_employee));
        } else if (!o.workflow_state || o.workflow_state.startsWith('Legacy')) {
            manual();
        }
    });
}
