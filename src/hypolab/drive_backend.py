"""Minimal Drive create/readback backend; never updates, removes or copies files.

The port is intended ONLY for a separately credentialed private OUTPUT identity.
Drive names are not unique; preflight + readback catches duplicates but does
NOT constitute a distributed atomic lock. The trusted owner journal supplies
single-writer admission. A failed create/readback is permanently ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import re
from typing import Protocol


class DriveBoundaryError(RuntimeError):
    pass


class DriveReadPort(Protocol):
    def list_named(self, parent_id: str, name: str) -> list[dict]: ...
    def metadata(self, file_id: str) -> dict: ...
    def download(self, file_id: str) -> bytes: ...


class DrivePort(DriveReadPort, Protocol):
    def create(self, parent_id: str, name: str, data: bytes, mime: str) -> dict: ...


@dataclass(frozen=True)
class VerifiedObject:
    file_id: str
    sha256: str
    size: int
    name: str
    parent_id: str


class VerifiedDriveReader:
    """Read-only receipt verifier, can run without any upload permission."""
    SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.json\Z")
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, port: DriveReadPort, *, private_folder_id: str,
                 forbidden_folder_ids: frozenset[str], max_bytes: int = MAX_BYTES):
        if (not private_folder_id or not forbidden_folder_ids
                or private_folder_id in forbidden_folder_ids
                or type(max_bytes) is not int or not 0 < max_bytes <= self.MAX_BYTES):
            raise DriveBoundaryError("invalid or source-overlapping destination pin")
        self.port = port
        self.parent_id = private_folder_id
        self.max_bytes = max_bytes

    def read_exact(self, file_id: str, *, name: str, expected_sha: str) -> bytes:
        if not file_id or self.SAFE_NAME.fullmatch(name) is None:
            raise DriveBoundaryError("invalid object reference")
        try:
            meta = self.port.metadata(file_id)
        except (KeyError, FileNotFoundError) as exc:
            raise DriveBoundaryError("private object missing") from exc
        if (meta.get("id") != file_id or meta.get("name") != name
                or meta.get("mimeType") != "application/json"
                or meta.get("parents") != [self.parent_id]
                or meta.get("trashed") is not False):
            raise DriveBoundaryError("private object metadata changed")
        try:
            sz = int(meta["size"])
        except (KeyError, ValueError, TypeError) as e:
            raise DriveBoundaryError("private object size missing") from e
        if not 0 < sz <= self.max_bytes:
            raise DriveBoundaryError("private object size outside cap")
        content = self.port.download(file_id)
        if (type(content) is not bytes or len(content) != sz
                or hashlib.sha256(content).hexdigest() != expected_sha):
            raise DriveBoundaryError("private object content readback mismatch")
        files = self.port.list_named(self.parent_id, name)
        if len(files) != 1 or files[0].get("id") != file_id:
            raise DriveBoundaryError("Drive filename collision or missing object")
        return content



class VerifiedDriveWriter(VerifiedDriveReader):
    """Create-only uploader; requires an independent private writer identity."""

    def write_once(self, name: str, content: bytes) -> VerifiedObject:
        if (type(name) is not str or self.SAFE_NAME.fullmatch(name) is None
                or type(content) is not bytes or not 0 < len(content) <= self.max_bytes):
            raise DriveBoundaryError("invalid upload name or byte count")
        if self.port.list_named(self.parent_id, name):
            raise DriveBoundaryError("output name already occupied; never replace")
        sha = hashlib.sha256(content).hexdigest()
        # One create attempt. On uncertainty do NOT call again or delete a file.
        created = self.port.create(self.parent_id, name, content, "application/json")
        file_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise DriveBoundaryError("Drive create had ambiguous outcome; no retry")
        self.read_exact(file_id, name=name, expected_sha=sha)
        return VerifiedObject(file_id, sha, len(content), name, self.parent_id)


class GoogleDriveV3Port:
    """Production-adjacent adapter; inject independently scoped Drive v3 service.

    The SDK is an OPTIONAL runtime dependency. Authentication must be configured
    *outside* this module. No default_credentials() or broad OAuth scopes here.
    """
    def __init__(self, service):
        self.service = service

    def list_named(self, parent_id: str, name: str) -> list[dict]:
        # Strict client-side exact match; Drive returns same-name duplicates.
        escaped_name = name.replace("'", "\\'")
        escaped_parent = parent_id.replace("'", "\\'")
        result = self.service.files().list(
            q=f"'{escaped_parent}' in parents and name = '{escaped_name}' and trashed = false",
            fields="nextPageToken,files(id,name,parents,trashed)",page_size=100,
            spaces="drive",supportsAllDrives=True,includeItemsFromAllDrives=True
        ).execute()
        if result.get("nextPageToken") is not None:
            raise DriveBoundaryError("ambiguous large listing; fail closed")
        rows = result.get("files", [])
        if not isinstance(rows, list) or any(x.get("name") != name for x in rows):
            raise DriveBoundaryError("Drive listing malformed")
        return rows

    def create(self, parent_id: str, name: str, data: bytes, mime: str) -> dict:
        try:
            from googleapiclient.http import MediaIoBaseUpload
        except ImportError as e:
            raise DriveBoundaryError("install google-api-python-client for Drive uploads") from e
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        return self.service.files().create(
            body={"name":name,"parents":[parent_id],"mimeType":mime},
            media_body=media,fields="id",supportsAllDrives=True
        ).execute()

    def metadata(self, file_id: str) -> dict:
        return self.service.files().get(
            fileId=file_id,fields="id,name,mimeType,size,parents,trashed",
            supportsAllDrives=True).execute()

    def download(self, file_id: str) -> bytes:
        result = self.service.files().get_media(fileId=file_id,supportsAllDrives=True).execute()
        if type(result) is not bytes:
            raise DriveBoundaryError("nonbinary Drive readback")
        return result


class GoogleDriveV3ReadPort:
    """Read-only API surface for broker; no Drive create/update/delete methods."""
    def __init__(self, service):
        self.service = service

    def list_named(self, parent_id: str, name: str) -> list[dict]:
        return GoogleDriveV3Port(self.service).list_named(parent_id,name)

    def metadata(self, file_id: str) -> dict:
        return GoogleDriveV3Port(self.service).metadata(file_id)

    def download(self, file_id: str) -> bytes:
        return GoogleDriveV3Port(self.service).download(file_id)
