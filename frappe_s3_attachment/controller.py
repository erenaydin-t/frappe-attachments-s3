import datetime
import os
import random
import re
import string

from urllib.parse import quote

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

        # Optional custom endpoint for S3-compatible providers (ArvanCloud,
        # MinIO, Wasabi, DigitalOcean Spaces, ...). Blank means plain AWS S3.
        endpoint_url = self.s3_settings_doc.get('endpoint_url') or None
        config = Config(
            signature_version='s3v4',
            # Path-style addressing ({endpoint}/{bucket}/{key}) is the most
            # portable across S3-compatible providers; AWS keeps its default
            # auto/virtual-hosted behaviour when no endpoint is set.
            s3={'addressing_style': 'path'} if endpoint_url else {},
        )
        client_args = {
            'region_name': self.s3_settings_doc.region_name,
            'config': config,
        }
        if endpoint_url:
            client_args['endpoint_url'] = endpoint_url
        if aws_key and aws_secret:
            client_args['aws_access_key_id'] = aws_key
            client_args['aws_secret_access_key'] = aws_secret

        self.S3_CLIENT = boto3.client('s3', **client_args)
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
        # S3 object metadata must be ASCII-only, so percent-encode the file
        # name (it may contain non-ASCII characters, e.g. Persian/Arabic).
        # It is decoded again when building the download Content-Disposition.
        extra_args = {
            "ContentType": content_type,
            "Metadata": {
                "ContentType": content_type,
                "file_name": quote(file_name),
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
            frappe.throw(
                frappe._(
                    "File upload to cloud storage failed. Check the Bucket "
                    "Name, AWS Key/Secret, Region and Endpoint URL in S3 File "
                    "Attachment settings, then try again."
                ),
                title=frappe._("Cloud Upload Failed"),
            )
        return key

    def delete_from_s3(self, key):
        """ Delete file from s3"""
        if self.s3_settings_doc.delete_file_from_cloud:
            try:
                self.S3_CLIENT.delete_object(
                    Bucket=self.s3_settings_doc.bucket_name,
                    Key=key
                )
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                # The object is already gone — the desired end state (no copy
                # in the cloud) is met, so let the File record be deleted.
                if code in ("404", "NoSuchKey"):
                    return
                frappe.throw(
                    frappe._(
                        "Could not delete this file from cloud storage ({0}). "
                        "Check the credentials and bucket permissions in S3 "
                        "File Attachment settings."
                    ).format(code or "error"),
                    title=frappe._("Cloud Delete Failed"),
                )

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
            # RFC 5987: use filename* with UTF-8 percent-encoding so non-ASCII
            # names (e.g. Persian/Arabic) download with the correct file name,
            # with a plain ASCII filename= fallback for older clients.
            ascii_name = file_name.encode('ascii', 'ignore').decode() or 'file'
            params['ResponseContentDisposition'] = (
                "inline; filename=\"{0}\"; filename*=UTF-8''{1}".format(
                    ascii_name, quote(file_name)
                )
            )

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
        # The key (and file name) can contain spaces and other characters that
        # are unsafe in a URL query string — e.g. the parent doctype "Raven
        # Message" puts a space in the key. Percent-encode both so the stored
        # file_url stays a valid URL and the key round-trips back unchanged.
        file_url = "/api/method/{0}?key={1}&file_name={2}".format(
            method, quote(key), quote(doc.file_name)
        )
    else:
        file_url = '{}/{}/{}'.format(
            s3_upload.S3_CLIENT.meta.endpoint_url,
            s3_upload.BUCKET,
            quote(key)
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
    # Confirm the object still exists before redirecting, so the user gets a
    # clear message instead of an opaque S3 XML error page in the browser.
    try:
        s3_upload.S3_CLIENT.head_object(Bucket=s3_upload.BUCKET, Key=key)
    except ClientError as e:
        _throw_s3_error(e, action=frappe._("read this file from"))

    signed_url = s3_upload.get_url(key, file_name)
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = signed_url


def _throw_s3_error(error, action):
    """
    Translate a botocore ClientError into a clear, user-facing message.

    `action` is a short phrase such as "read this file from" or "delete this
    file from" that is woven into the message.
    """
    code = ""
    if hasattr(error, "response"):
        code = error.response.get("Error", {}).get("Code", "")

    if code in ("404", "NoSuchKey"):
        frappe.throw(
            frappe._(
                "This file is no longer available in cloud storage. "
                "It may have been deleted directly from the bucket."
            ),
            title=frappe._("File Not Found in Cloud"),
        )
    elif code == "NoSuchBucket":
        frappe.throw(
            frappe._(
                "The configured S3 bucket does not exist. Check the Bucket "
                "Name in S3 File Attachment settings."
            ),
            title=frappe._("Bucket Not Found"),
        )
    elif code in (
        "403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"
    ):
        frappe.throw(
            frappe._(
                "Access to cloud storage was denied. Check the AWS Key, AWS "
                "Secret, Region and Endpoint URL in S3 File Attachment settings."
            ),
            title=frappe._("Cloud Storage Access Denied"),
        )
    else:
        frappe.throw(
            frappe._("Could not {0} cloud storage: {1}").format(
                action, code or str(error)
            )
        )


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
        # See file_upload_to_s3: percent-encode so keys containing spaces or
        # other unsafe characters survive as a valid URL query string.
        file_url = "/api/method/{0}?key={1}&file_name={2}".format(
            method, quote(key), quote(doc.file_name)
        )
    else:
        file_url = '{}/{}/{}'.format(
            s3_upload.S3_CLIENT.meta.endpoint_url,
            s3_upload.BUCKET,
            quote(key)
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
