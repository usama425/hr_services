// Copyright (c) 2023, Elite Resources and contributors
// For license information, please see license.txt

frappe.provide("erpnext.job_offer");

// New offers go to Elite HQ for now. The Project field copies the applicant's
// project (blank when the applicant has none), so it is filled whenever it is empty.
const DEFAULT_PROJECT = { project: 'PROJ-0018', project_name: 'Elite HQ' };

function set_default_project(frm) {
	const new_flow = !(frm.doc.workflow_state || '').startsWith('Legacy');
	if (frm.doc.docstatus === 0 && new_flow && !frm.doc.custom_project) {
		frm.set_value('custom_project', DEFAULT_PROJECT.project);
		frm.set_value('custom_project_name', DEFAULT_PROJECT.project_name);
	}
}

frappe.ui.form.on('Job Offer', {
	job_applicant: function (frm) {
		// wait for the applicant's fields to be copied in first
		setTimeout(() => set_default_project(frm), 800);
	},
	custom_project: function (frm) {
		set_default_project(frm);
	},
	onload: function (frm) {
		if (frm.doc.__islocal) set_default_project(frm);
		frm.set_query("select_terms", function() {
			return { filters: { custom_for_job_offer: 1 } };
		});

		if(frm.doc.__islocal){
			var new_row = cur_frm.add_child('custom_salary_details');
			new_row.salary_components = 'Basic Salary';
			new_row.amount = 0.0;
			var new_row = cur_frm.add_child('custom_salary_details');
			new_row.salary_components = 'Housing Allowance';
			new_row.amount = 0.0;
			var new_row = cur_frm.add_child('custom_salary_details');
			new_row.salary_components = 'Transportations Allowance';
			new_row.amount = 0.0;
			cur_frm.refresh_field("custom_salary_details");
		}
	},
	before_save(frm){
		if(frm.doc.custom_salary_details){
			calculate_salary(frm);
		}
	},
	refresh: function (frm) {
		const in_flow = frm.doc.workflow_state && !frm.doc.workflow_state.startsWith('Legacy');
		if (in_flow) {
			add_recruitment_buttons(frm);
		}
		if (!in_flow && (!frm.doc.__islocal) && (frm.doc.status == 'Accepted') && (frm.doc.docstatus === 1) && (!frm.doc.__onload || !frm.doc.__onload.employee)) {
			if(frm.doc.custom_nationality == "Saudi"){
				if((frm.doc.custom_qiwa_status == 'Accepted by Candidate' || frm.doc.custom_qiwa_status == 'Accepted by His/Her Sponsor') 
					&& (frm.doc.custom_gosi_status == 'Accepted')){
						frm.add_custom_button(__('Create Employee'),
							function () {
								erpnext.job_offer.make_employee(frm);
							}
						).addClass('btn-primary');;
				}
			}
			if(frm.doc.custom_nationality == "Non Saudi"){
				if(frm.doc.custom_qiwa_status == 'Accepted by Candidate' || frm.doc.custom_qiwa_status == 'Accepted by His/Her Sponsor'){
					frm.add_custom_button(__('Create Employee'),
						function () {
							erpnext.job_offer.make_employee(frm);
						}
					).addClass('btn-primary');;
					
				}
			}
		}
		if (!in_flow && (!frm.doc.__islocal) && (frm.doc.status == 'Accepted') && (frm.doc.custom_job_title_on_visa)
			&& (frm.doc.docstatus === 1) && (!frm.doc.__onload || !frm.doc.__onload.employee)) {
			frm.add_custom_button(__('Create Contract'),
				function () {
					erpnext.job_offer.make_contract(frm);
				}
			).addClass('btn-primary');
		}
		if(frm.doc.custom_project && frm.doc.docstatus == 0){
			if(frm.doc.custom_project == "PROJ-0002"){ //ACWA-NEOM project
				frm.set_value("select_terms","Neom");
			}
			else if(frm.doc.custom_project == "PROJ-0001"){ //Misk project
				frm.set_value("select_terms","Misk");
			}
			else if(frm.doc.custom_project == "PROJ-0005"){ //Cummins project
				frm.set_value("select_terms","Cummins");
			}
			else if(frm.doc.custom_project == "PROJ-0022" && frm.doc.custom_divisions == "Hittin"){ //Riyadh Schools Platform and division == Hittin project
				frm.set_value("select_terms","RSP Hittin");
			}
			else if(frm.doc.custom_project == "PROJ-0022" && frm.doc.custom_divisions == "Admin"){ //Riyadh Schools Platform and division == Admin project
				frm.set_value("select_terms","RSP Admin");
			}
			else if(frm.doc.custom_project == "PROJ-0022" && frm.doc.custom_divisions == "Al-Salam"){ //Riyadh Schools Platform and division == Al-Salam project
				frm.set_value("select_terms","RSP Al-Salam");
			}
			else{
				frm.set_value("select_terms","Elite");
			}
		}

	},
	custom_candidate_category: function(frm) {
		// Applicant Type / Nationality are derived from this on save; set Country now.
		if (frm.doc.custom_candidate_category == 'Saudi') {
			frm.set_value("custom_country", "Saudi Arabia");
		} else if (frm.doc.custom_candidate_category && frm.doc.job_applicant
			&& (!frm.doc.custom_country || frm.doc.custom_country == "Saudi Arabia")) {
			frappe.db.get_value('Job Applicant', frm.doc.job_applicant, 'custom_nationality').then((r) => {
				const country = r.message && r.message.custom_nationality;
				frm.set_value("custom_country", country && country != "Saudi Arabia" ? country : "");
			});
		}
	},
	custom_recruitment_agency: function(frm) {
		if (!frm.doc.custom_recruitment_agency) return;
		frappe.db.get_value('Supplier', frm.doc.custom_recruitment_agency, ['email_id', 'mobile_no', 'country']).then((r) => {
			const s = r.message || {};
			if (s.email_id) frm.set_value("custom_agency_email", s.email_id);
			if (s.mobile_no) frm.set_value("custom_agency_contact_no", s.mobile_no);
			if (s.country && !frm.doc.custom_agency_country) frm.set_value("custom_agency_country", s.country);
		});
	},
	custom_nationality: function(frm) {
		if(frm.doc.custom_nationality == 'Saudi'){
			frm.set_value("custom_country", "Saudi Arabia");
		}
		else if(frm.doc.custom_nationality == 'Non Saudi'){
			frappe.call({
				'method': 'frappe.client.get_value',
				'args': {
					'doctype': 'Job Applicant',
					'filters': {
						'name': frm.doc.job_applicant
					},
				   'fieldname':'custom_nationality'
				},
				'callback': function(res){
					frm.set_value("custom_country",res.message.custom_nationality);
				}
			});
		}
		else{
			frm.set_value("custom_country", "");
		}
	},
	custom_applicant_type: function(frm){
		if(frm.doc.custom_applicant_type == 'Local'){
			frm.set_value("custom_job_title_on_visa", "");
			frm.set_value("custom_nationality", "");
			frm.set_df_property('custom_nationality','read_only',0);
		}
		else if(frm.doc.custom_applicant_type == 'Overseas'){
			frm.set_value("custom_qiwa_status", "");
			frm.set_value("custom_gosi_status", "");
			frm.set_value("custom_nationality", "Non Saudi");
			frm.set_df_property('custom_nationality','read_only',1);
			frappe.call({
				'method': 'frappe.client.get_value',
				'args': {
					'doctype': 'Job Applicant',
					'filters': {
						'name': frm.doc.job_applicant
					},
				   'fieldname':'custom_nationality'
				},
				'callback': function(res){
					frm.set_value("custom_country",res.message.custom_nationality);
				}
			});
		}
		else{
			frm.set_value("custom_qiwa_status", "");
			frm.set_value("custom_gosi_status", "");
			frm.set_value("custom_job_title_on_visa", "");
			frm.set_value("custom_nationality", "");
			frm.set_df_property('custom_nationality','read_only',0);
		}
	},
	offer_date: function(frm){
		convert_into_hijri(frm);
	},
	select_terms: function (frm) {
		if(frm.doc.select_terms){
			frappe.call({
				method: 'hr_services.custompy.job_offer.get_terms_and_conditions',
				args: {
					template_name: frm.doc.select_terms,
					doc: frm.doc
				},
				callback: function(r) {
					if (!r.exc) {
						frm.set_value("custom_terms_and_conditions_in_arabic", r.message);
					}
				}
			})
		}
	},
});

frappe.ui.form.on("Contract Details", {
	amount:function(frm, cdt, cdn){
		calculate_salary(frm);
	},
	custom_salary_details_remove(frm,cdt,cdn){
		calculate_salary(frm);
	}
});

function calculate_salary(frm){
    let total = 0;
	for(let i in frm.doc.custom_salary_details){
		total += frm.doc.custom_salary_details[i].amount;
	}
	frm.set_value("custom_total_salary", total);
	frm.refresh();
}

erpnext.job_offer.make_employee = function (frm) {
	frappe.model.open_mapped_doc({
		method: "hr_services.custompy.job_offer.make_employee",
		frm: frm
	});
};

erpnext.job_offer.make_contract = function (frm) {
	frappe.model.open_mapped_doc({
		method: "hr_services.custompy.job_offer.make_contract",
		frm: frm
	});
};

function convert_into_hijri(frm){
    frappe.call({
        method: "hr_services.custompy.employee.convert_into_hijri",
        args: {
            date: frm.doc.offer_date
        },
        callback: function(r){
            if(r.message){
                frm.set_value("custom_offer_date_in_hijri",r.message);
            }
        }
    });
}
// Recruitment flow (workflow "Job Offer Recruitment"): the stage buttons come from the
// workflow; these add the contract / employee shortcuts at the right stages.
function add_recruitment_buttons(frm) {
	// The employee is created automatically when the contract is signed.
	frm.remove_custom_button(__('Create Employee'));
	const ready_for_contract = ['Visa Issued', 'Transfer Completed'].includes(frm.doc.workflow_state);
	if (ready_for_contract && frm.doc.docstatus === 1) {
		frm.add_custom_button(__('Create Contract'), function () {
			erpnext.job_offer.make_contract(frm);
		}).addClass('btn-primary');
	}
	if (frm.doc.custom_employee) {
		frm.add_custom_button(__('Show Employee'), function () {
			frappe.set_route("Form", "Employee", frm.doc.custom_employee);
		});
	}
	if (frm.doc.workflow_state == 'Contract Sent') {
		frm.dashboard.set_headline(
			__('Waiting for the contract to be signed. Tick "Is Signed" on the contract and attach the signed copy; the employee is then created automatically (Under Recruitment).')
		);
	}
}
