import frappe

JOB = "hr_services.teams_excel_notify.tasks.poll_all"


def quiet_scheduler_log():
	"""The check runs every minute, so skip the 1,440 Scheduled Job Log rows a day it would create.

	Frappe forces logging for cron jobs only when the job type is saved from the form,
	so setting the flag directly sticks. Failures are still written to the Error Log.
	"""
	if frappe.db.exists("Scheduled Job Type", {"method": JOB}):
		frappe.db.set_value("Scheduled Job Type", {"method": JOB}, "create_log", 0, update_modified=False)
