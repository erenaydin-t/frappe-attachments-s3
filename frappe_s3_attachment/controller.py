import datetime
import os
import random
import re
import string

import boto3

from botocore.client import Config
from botocore.exceptions import ClientError

import frappe


import magic


class S3Operations:

    def __init__(self):
        """
        Function to initialise the aws settings from frappe S3 File attachment
        doctype.
        """
        self.s3_settings_doc = frappe.get_doc(
            'S3 File Attachment',
            'S3 File Attachment',
        )
        aws_key = self.s3_settings_doc.aws_key
        aws_secret = (
            self.s3_settings_doc.get_password('aws_secret')
            if self.s3_settings_doc.get('aws_secret') else None
        )
        if aws_key and aws_secret:
            self.S3_CLIENT = boto3.client(
                's3',
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                region_name=self.s3_settings_doc.region_name,
                config=Config(signature_version='s3v4')
            )
        else:
            self.S3_CLIENT = boto3.client(
                's3',
                region_name=self.s3_settings_doc.region_name,
                config=Config(signature_version='s3v4')
            )
        self.BUCKET = self.s3_settings_doc.bucket_name
        self.folder_name = self.s3_settings_doc.folder_name

    def strip_special_chars(self, file_name):
        """
        Strips file charachters which doesnt match the regex.
        """
        regex = re.compile('[^0-9a-zA-Z._-]')
        file_name = regex.sub('', file_name)
        return file_name

    def key_generator(self, file_name, parent_doctype, parent_name):
        """
        Generate keys for s3 objects uploaded with file name attached.
        """
        hook_cmd = frappe.get_hooks().get("s3_key_generator")
        if hook_cmd:
            try:
                k = frappe.get_attr(hook_cmd[0])(
                    file_name=file_name,
                    parent_doctype=parent_doctype,
                    parent_name=parent_name
                )
                if k:
                    return k.rstrip('/').lstrip('/')
            except Exception:
                pass

        file_name = file_name.replace(' ', '_')
        file_name = self.strip_special_chars(file_name)
        key = ''.join(
            random.choice(
                string.ascii_uppercase + string.digits) for _ in range(8)
        )

        today = datetime.datetime.now()
        year = today.strftime("%Y")
        month = today.strftime("%m")
        day = today.strftime("%d")

        doc_path = None

        if not doc_path:
            if self.folder_name:
                final_key = self.folder_name + "/" + year + "/" + month + \
                    "/" + day + "/" + parent_doctype + "/" + key + "_" + \
                    file_name
            else:
                final_key = year + "/" + month + "/" + day + "/" + \
                    parent_doctype + "/" + key + "_" + file_name
            return final_key
        else:
            final_key = doc_path + '/' + key + "_" + file_name
            return final_key

    def upload_files_to_s3_with_key(
            self, file_path, file_name, is_private, parent_doctype, parent_name
    ):
        """
        Uploads a new file to S3.
        Strips the file extension to set the content_type in metadata.
        """
        mime_type = magic.from_file(file_path, mime=True)
        key = self.key_generator(file_name, parent_doctype, parent_name)
        content_type = mime_type
        extra_args = {
            "ContentType": content_type,
            "Metadata": {
                "ContentType": content_type,
                "file_name": file_name,
            },
        }
        if not is_private:
            extra_args["ACL"] = "public-read"
        try:
            self.S3_CLIENT.upload_file(
                file_path, self.BUCKET, key, ExtraArgs=extra_args
            )
        except boto3.exceptions.S3UploadFailedError:
            # Buckets created with "Bucket owner enforced" object ownership
            # (the AWS default since April 2023) have ACLs disabled, so a
            # public-read upload is rejected. Retry once without the ACL; the
            # object is then served via the bucket policy / presigned URL.
            if "ACL" in extra_args:
                extra_args.pop("ACL")
                try:
                    self.S3_CLIENT.upload_file(
                        file_path, self.BUCKET, key, ExtraArgs=extra_args
                    )
                    return key
                except boto3.exceptions.S3UploadFailedError:
                    pass
            frappe.throw(frappe._("File Upload Failed. Please try again."))
        return key

    def delete_from_s3(self, key):
        """ Delete file from s3"""
        if self.s3_settings_doc.delete_file_from_cloud:
            try:
                self.S3_CLIENT.delete_object(
                    Bucket=self.s3_settings_doc.bucket_name,
                    Key=key
                )
            except ClientError:
                frappe.throw(frappe._("Access denied: Could not delete file"))

    def read_file_from_s3(self, key):
        """
        Function to read file from a s3 file.
        """
        return self.S3_CLIENT.get_object(Bucket=self.BUCKET, Key=key)

    def get_url(self, key, file_name=None):
        """
        Return url.

        :param bucket: s3 bucket name
        :param key: s3 object key
        """
        if self.s3_settings_doc.signed_url_expiry_time:
            self.signed_url_expiry_time = self.s3_settings_doc.signed_url_expiry_time # noqa
        else:
            self.signed_url_expiry_time = 120
        params = {
                'Bucket': self.BUCKET,
                'Key': key,

        }
        if file_name:
            params['ResponseContentDisposition'] = 'filename={}'.format(file_name)

        url = self.S3_CLIENT.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=self.signed_url_expiry_time,
        )

        return url


def is_s3_configured():
    """Return True only when a bucket has been set in the settings doctype."""
    return bool(frappe.db.get_single_value('S3 File Attachment', 'bucket_name'))


def is_s3_uploadable(doc):
    """
    Return True only for locally-stored files that should be offloaded to S3.

    Skips folders (the File doctype is also used for the folder tree), records
    without content, externally hosted files, and files already pushed to S3
    (e.g. when the migration tool is re-run).
    """
    if getattr(doc, "is_folder", 0):
        return False
    if not doc.file_url:
        return False
    if doc.file_url.startswith(("http://", "https://")):
        return False
    if doc.file_url.startswith("/api/method/frappe_s3_attachment"):
        return False
    return True


@frappe.whitelist()
def file_upload_to_s3(doc, method):
    """
    Upload a freshly created File to S3 and rewrite its file_url.
    """
    parent_doctype = doc.attached_to_doctype or 'File'
    parent_name = doc.attached_to_name
    ignore_s3_upload_for_doctype = frappe.local.conf.get(
        'ignore_s3_upload_for_doctype') or ['Data Import']
    if parent_doctype in ignore_s3_upload_for_doctype:
        return
    if not is_s3_uploadable(doc) or not is_s3_configured():
        return

    file_path = doc.get_full_path()
    if not os.path.exists(file_path):
        return

    s3_upload = S3Operations()
    key = s3_upload.upload_files_to_s3_with_key(
        file_path, doc.file_name, doc.is_private, parent_doctype, parent_name
    )

    if doc.is_private:
        method = "frappe_s3_attachment.controller.generate_file"
        file_url = "/api/method/{0}?key={1}&file_name={2}".format(
            method, key, doc.file_name
        )
    else:
        file_url = '{}/{}/{}'.format(
            s3_upload.S3_CLIENT.meta.endpoint_url,
            s3_upload.BUCKET,
            key
        )

    os.remove(file_path)
    # Store the S3 object key in content_hash so the file can be located again
    # for presigned downloads, permission checks and deletion.
    doc.db_set(
        {"file_url": file_url, "content_hash": key}, update_modified=False
    )
    doc.file_url = file_url

    image_field = (
        frappe.get_meta(parent_doctype).get("image_field")
        if parent_doctype else None
    )
    if image_field and parent_name:
        frappe.db.set_value(parent_doctype, parent_name, image_field, file_url)

    frappe.db.commit()


@frappe.whitelist()
def generate_file(key=None, file_name=None):
    """
    Stream a private file from S3 via a short-lived presigned URL.
    """
    if not key:
        frappe.local.response['body'] = "Key not found."
        return

    # Frappe's built-in /private/files route enforces read permission on the
    # attached document; this redirect bypasses it, so re-check here.
    attached = frappe.db.get_value(
        'File', {'content_hash': key},
        ['attached_to_doctype', 'attached_to_name'], as_dict=True
    )
    if attached and attached.attached_to_doctype and attached.attached_to_name:
        if not frappe.has_permission(
            attached.attached_to_doctype,
            doc=attached.attached_to_name,
            ptype='read'
        ):
            raise frappe.PermissionError(
                frappe._("You do not have permission to access this file.")
            )

    s3_upload = S3Operations()
    signed_url = s3_upload.get_url(key, file_name)
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = signed_url


def upload_existing_files_s3(name):
    """
    Upload a single existing local file to S3 (used by the migration tool).
    """
    if not frappe.db.exists('File', name):
        return
    doc = frappe.get_doc('File', name)
    if not is_s3_uploadable(doc) or not is_s3_configured():
        return

    file_path = doc.get_full_path()
    if not os.path.exists(file_path):
        return

    parent_doctype = doc.attached_to_doctype or 'File'
    parent_name = doc.attached_to_name
    s3_upload = S3Operations()
    key = s3_upload.upload_files_to_s3_with_key(
        file_path, doc.file_name, doc.is_private, parent_doctype, parent_name
    )

    if doc.is_private:
        method = "frappe_s3_attachment.controller.generate_file"
        file_url = "/api/method/{0}?key={1}&file_name={2}".format(
            method, key, doc.file_name
        )
    else:
        file_url = '{}/{}/{}'.format(
            s3_upload.S3_CLIENT.meta.endpoint_url,
            s3_upload.BUCKET,
            key
        )

    # Remove file from local.
    os.remove(file_path)
    doc.db_set(
        {"file_url": file_url, "content_hash": key}, update_modified=False
    )
    frappe.db.commit()


def s3_file_regex_match(file_url):
    """
    Match the public file regex match.
    """
    return re.match(
        r'^(https:|/api/method/frappe_s3_attachment.controller.generate_file)',
        file_url
    )


@frappe.whitelist()
def migrate_existing_files():
    """
    Function to migrate the existing files to s3.
    """

    files_list = frappe.get_all(
        'File',
        fields=['name', 'file_url']
    )
    for file in files_list:
        if file['file_url']:
            if not s3_file_regex_match(file['file_url']):
                upload_existing_files_s3(file['name'])
    return True


def delete_from_cloud(doc, method):
    """Delete file from s3 when the File doc is trashed."""
    if not doc.content_hash or not is_s3_configured():
        return
    s3 = S3Operations()
    s3.delete_from_s3(doc.content_hash)


@frappe.whitelist()
def ping():
    """
    Test function to check if api function work.
    """
    return "pong"
