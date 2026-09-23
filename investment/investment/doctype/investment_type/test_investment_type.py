# Copyright (c) 2026, Aagnya Mistry and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

# Account's link chain reaches Payment Gateway (payments app, not installed); this test needs no Account records
IGNORE_TEST_RECORD_DEPENDENCIES = ["Account"]


class TestInvestmentType(IntegrationTestCase):
	def test_create_investment_type(self):
		doc = frappe.get_doc(
			{
				"doctype": "Investment Type",
				"investment_type": "Test Bank FD",
				"instrument_class": "Deposit",
			}
		).insert()

		self.assertEqual(doc.name, "Test Bank FD")
		self.assertEqual(doc.is_active, 1)

		doc.delete()
