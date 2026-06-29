<a href="https://zerodha.tech"><img src="https://zerodha.tech/static/images/github-badge.svg" align="right" /></a>

## Frappe S3 Attachment

Frappe app to make file upload automatically upload and read from s3.

#### Features.

1. Upload both public and private files to s3.
2. Stream files from S3, when file is viewed everytime.
3. Lets you add S3 credentials
    (aws key, aws secret, bucket name, folder name) through ui and migrate existing
    files.
4. Deletes from s3 whenever a file is deleted in ui.
5. Files are uploaded categorically in the format.
    {s3_folder_path}/{year}/{month}/{day}/{doctype}/{file_hash}

#### Installation.

1. bench get-app https://github.com/erenaydin-t/frappe-attachments-s3.git --branch main
2. bench --site <your-site> install-app frappe_s3_attachment

#### Configuration Setup.

1. Open single doctype "S3 File Attachment".
2. Enter **Bucket Name, AWS Key, AWS Secret, S3 Bucket Region Name** and (optionally)
   **Folder Name** — the default folder path/prefix inside the bucket.
3. **Endpoint URL (S3-compatible)** — leave blank for AWS S3. For other S3-compatible
   providers set the full endpoint, e.g.
   - ArvanCloud: `https://s3.ir-thr-at1.arvanstorage.ir` (region `ir-thr-at1`)
   - MinIO / Wasabi / DigitalOcean Spaces are also supported.
   When an endpoint is set, path-style addressing (`{endpoint}/{bucket}/{key}`) is used
   for maximum compatibility.
4. **Signed URL expiry time** — how long (in seconds) a private-file download link stays
   valid. Defaults to 120s if left empty.

#### The two action settings explained.

**Delete file from cloud** (checkbox, default **off**)
- **On:** deleting a file in ERPNext also **permanently deletes the object from the
  bucket**. Use with care — there is no undo.
- **Off:** the file is removed from ERPNext, but the copy in the bucket is **kept**
  (safer; good if the bucket is also your backup/archive).

**Migrate Existing Files** (button)
- Uploads every file currently stored locally (both public and private) to S3, rewrites
  each file's link to point at S3, and deletes the local copy **only after** a successful
  upload.
- Files already on S3 are skipped, so it is safe to re-run (e.g. after adding more files).
- Run it once after you have entered and saved the credentials.

#### What changes for end users (UI side effects).

- **Uploading:** unchanged — users attach files the normal way. Behind the scenes the file
  is pushed to S3 and the local copy on the server is removed.
- **Viewing public files:** the file link points directly at the bucket object.
- **Viewing private files:** the link becomes
  `/api/method/frappe_s3_attachment.controller.generate_file?...`. When clicked, ERPNext
  checks the user's permission on the attached document and then redirects to a short-lived
  **signed URL** (expires per *Signed URL expiry time*). Permissions are enforced exactly
  as before — users only see files on documents they can read.
- **Deleting:** behaves per the *Delete file from cloud* setting above.

#### Error handling — what users see when something goes wrong.

| Situation | What happens |
| --- | --- |
| **File missing in bucket** (object was deleted directly from S3) | Opening a private file shows **"This file is no longer available in cloud storage."** instead of a raw S3 error page. |
| **Wrong / non-existent bucket** | Upload and view show a clear message pointing to the **Bucket Name** setting ("Bucket Not Found"). |
| **Bad credentials / endpoint / region** | Operations show **"Access to cloud storage was denied. Check the AWS Key, AWS Secret, Region and Endpoint URL"**. |
| **Upload fails** | The user is told the upload failed and to check Bucket/credentials/endpoint; the file is not silently lost. |
| **Deleting a file whose object is already gone** | The ERPNext file record is still deleted normally (a missing object is treated as already-deleted, so it does not block you). |
| **Cloud delete blocked by permissions** | Deletion is stopped with **"Could not delete this file from cloud storage … Check the credentials and bucket permissions."** |

> Note: files are streamed/served from S3 on every view. If the bucket, credentials, or
> network to the provider are unavailable, existing attachments will not load until access
> is restored — the file links depend on the bucket being reachable.

#### License

MIT
