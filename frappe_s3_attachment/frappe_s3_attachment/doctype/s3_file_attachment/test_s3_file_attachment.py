# Copyright (c) 2018, Frappe and Contributors
# See license.txt

import unittest

import frappe

from frappe_s3_attachment import controller


class TestS3FileAttachment(unittest.TestCase):
	def test_ping(self):
		self.assertEqual(controller.ping(), "pong")

	def test_s3_file_regex_match(self):
		self.assertTrue(
			controller.s3_file_regex_match("https://bucket.s3.amazonaws.com/key")
		)
		self.assertTrue(
			controller.s3_file_regex_match(
				"/api/method/frappe_s3_attachment.controller.generate_file?key=x"
			)
		)
		self.assertFalse(
			bool(controller.s3_file_regex_match("/files/local-file.png"))
		)

	def test_is_s3_uploadable_skips_folders(self):
		doc = frappe._dict({"is_folder": 1, "file_url": "/files/x.png"})
		self.assertFalse(controller.is_s3_uploadable(doc))

	def test_is_s3_uploadable_skips_empty(self):
		doc = frappe._dict({"is_folder": 0, "file_url": None})
		self.assertFalse(controller.is_s3_uploadable(doc))

	def test_is_s3_uploadable_skips_remote(self):
		doc = frappe._dict({"is_folder": 0, "file_url": "https://example.com/x"})
		self.assertFalse(controller.is_s3_uploadable(doc))

	def test_is_s3_uploadable_skips_already_migrated(self):
		doc = frappe._dict({
			"is_folder": 0,
			"file_url": "/api/method/frappe_s3_attachment.controller.generate_file?key=x",
		})
		self.assertFalse(controller.is_s3_uploadable(doc))

	def test_is_s3_uploadable_accepts_local_public(self):
		doc = frappe._dict({"is_folder": 0, "file_url": "/files/x.png"})
		self.assertTrue(controller.is_s3_uploadable(doc))

	def test_is_s3_uploadable_accepts_local_private(self):
		doc = frappe._dict({"is_folder": 0, "file_url": "/private/files/x.png"})
		self.assertTrue(controller.is_s3_uploadable(doc))

	_UNSET = object()

	def _set_defer_conf(self, value):
		"""Set s3_defer_upload_for_doctype in conf, restoring it on cleanup."""
		previous = frappe.local.conf.get("s3_defer_upload_for_doctype", self._UNSET)

		def restore():
			if previous is self._UNSET:
				frappe.local.conf.pop("s3_defer_upload_for_doctype", None)
			else:
				frappe.local.conf["s3_defer_upload_for_doctype"] = previous

		self.addCleanup(restore)
		if value is self._UNSET:
			frappe.local.conf.pop("s3_defer_upload_for_doctype", None)
		else:
			frappe.local.conf["s3_defer_upload_for_doctype"] = value

	def test_deferred_doctypes_default(self):
		self._set_defer_conf(self._UNSET)
		self.assertEqual(
			controller._deferred_upload_doctypes(), ["Raven Message"]
		)

	def test_deferred_doctypes_string_config(self):
		self._set_defer_conf("Raven Message")
		self.assertEqual(
			controller._deferred_upload_doctypes(), ["Raven Message"]
		)

	def test_deferred_doctypes_list_config(self):
		self._set_defer_conf(["Raven Message", "Custom DocType"])
		self.assertEqual(
			controller._deferred_upload_doctypes(),
			["Raven Message", "Custom DocType"],
		)
