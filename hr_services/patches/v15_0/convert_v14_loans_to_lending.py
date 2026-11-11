# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

# Kept for sites that already logged this patch. The check now also runs after
# every migrate and right after Lending is installed (see lending_compat).


def execute():
	from hr_services.lending_compat import convert_v14_loans_if_needed

	convert_v14_loans_if_needed()
