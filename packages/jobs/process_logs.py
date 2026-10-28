"""Controlled process files; no execution, retry, retention loop or caller paths.

Reuse the original background log policy (plain controlled directory and direct
files) and the existing no-follow directory pinning used by retention. stdout
and stderr share an unbuffered append file, just as background_runner did.
"""
from contextlib import contextmanager
import base64
import codecs
import os
from pathlib import Path
import stat

from packages.jobs import final_contracts as contracts

MAX_READ = 65536
MAX_OFFSET = 2**63 - 1


def reference(doc):
    from packages.jobs.independent_executor import fence
    return {"kind": "process-log", "version": 1,
            "id": "log_" + contracts.digest({"effectId": doc["effectId"], "fence": fence(doc["attempt"])}),
            "jobId": doc["jobId"], "effectId": doc["effectId"],
            "effectKey": dict(doc["effectKey"]),
            "attempt": {key: doc["attempt"][key] for key in
                        ("attemptId", "leaseId", "runId", "stepId", "ownerEpoch", "generation")},
            "streams": ["stdout", "stderr"], "cursorUnit": "byte"}


def bounds(offset, limit):
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise contracts.ContractError("invalid_schema", "offset must be an integer 0..2^63-1")
    if type(limit) is not int or not 4 <= limit <= MAX_READ:
        raise contracts.ContractError("invalid_schema", "limit must be an integer 4..65536 bytes")


def validate_page(page, doc, *, offset, limit):
    """Do not trust a remote reader to choose a file or expand the byte window."""
    fields = {"logRef", "offset", "nextOffset", "eof", "sizeBytes", "bytesBase64", "text", "encoding", "pendingBytes", "pendingBase64"}
    if not isinstance(page, dict) or set(page) != fields or page["logRef"] != reference(doc):
        raise ValueError("Original log response reference/schema mismatch")
    if (page["offset"] != offset or type(page["nextOffset"]) is not int
            or type(page["sizeBytes"]) is not int or not 0 <= page["sizeBytes"] <= MAX_OFFSET
            or type(page["eof"]) is not bool or type(page["pendingBytes"]) is not int
            or not 0 <= page["pendingBytes"] <= 3
            or not isinstance(page["bytesBase64"], str) or len(page["bytesBase64"]) > (limit + 2) // 3 * 4
            or not isinstance(page["pendingBase64"], str) or len(page["pendingBase64"]) > 4):
        raise ValueError("Original log response byte window mismatch")
    raw = base64.b64decode(page["bytesBase64"], validate=True)
    pending = base64.b64decode(page["pendingBase64"], validate=True)
    if (len(raw) + len(pending) > limit or len(pending) != page["pendingBytes"]
            or page["nextOffset"] != offset + len(raw) or page["nextOffset"] > page["sizeBytes"]
            or page["eof"] != (page["nextOffset"] == page["sizeBytes"])):
        raise ValueError("Original log response cursor mismatch")
    if page["encoding"] == "utf-8":
        if not isinstance(page["text"], str) or page["text"].encode("utf-8") != raw:
            raise ValueError("Original log response text/byte mismatch")
    elif page["encoding"] != "binary" or page["text"] is not None or pending:
        raise ValueError("Original log response encoding mismatch")
    return page


@contextmanager
def open_log(root, doc, *, write=False):
    from packages.core.background_jobs import _controlled_logs_dir, _path_has_reparse_component
    from packages.core.logging_setup import _is_plain_file_entry
    from packages.core.workdir_retention import pinned_directories
    root = Path(root)
    if doc.get("logRef") != reference(doc):
        raise contracts.ContractError("reference_conflict", "Original process log reference unavailable or inconsistent")
    if _path_has_reparse_component(root):
        raise contracts.ContractError("reference_conflict", "Controlled log root contains a link/reparse point")
    if write:
        (root / "logs").mkdir(exist_ok=True)
    directory, error = _controlled_logs_dir(root)
    if error or directory is None:
        raise contracts.ContractError("reference_conflict", "Controlled log directory unavailable")
    info = directory.lstat()
    snapshot = [("logs", info.st_dev, info.st_ino, info.st_mode)]
    name = reference(doc)["id"] + ".log"
    path = directory / name
    with pinned_directories(root, root, snapshot) as (_, handles):
        if path.exists() or path.is_symlink():
            if not _is_plain_file_entry(path):
                raise contracts.ContractError("reference_conflict", "Process log is not a unique plain file")
        elif not write:
            raise contracts.ContractError("provider_unavailable", "Original process log file unavailable; execution is not retried")
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT if write else os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        parent = handles.get(directory) if os.name != "nt" else None
        fd = os.open(name if parent is not None else path, flags, 0o600,
                     **({"dir_fd": parent} if parent is not None else {}))
        try:
            opened = os.fstat(fd)
            entry = path.lstat()
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or not _is_plain_file_entry(path)
                    or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)):
                raise contracts.ContractError("reference_conflict", "Process log identity changed")
            with os.fdopen(fd, "ab" if write else "rb", buffering=0) as stream:
                fd = None
                yield stream
        finally:
            if fd is not None:
                os.close(fd)


def read_log(root, doc, *, offset=0, limit=MAX_READ):
    """Byte cursor; text never consumes a partial Unicode character.

    Valid UTF-8 pages end at a character boundary. An unfinished trailing code
    point returns pendingBytes and leaves that suffix for the next read. Invalid
    bytes are returned losslessly as base64 with text=null, never replacement
    characters. No file is read beyond the caller's bounded byte window.
    """
    bounds(offset, limit)
    with open_log(root, doc) as stream:
        size = os.fstat(stream.fileno()).st_size
        if offset > size:
            raise contracts.ContractError("invalid_schema", "offset is beyond original log size")
        stream.seek(offset)
        raw = stream.read(min(limit, size - offset))  # A bounded snapshot while the process appends.
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        text = decoder.decode(raw, final=False)
        pending = decoder.getstate()[0]
        consumed = len(raw) - len(pending)
        # A continuation byte at a caller-selected offset is not a valid text
        # boundary. Invalid original output is still available as exact bytes.
        result = {"text": text, "encoding": "utf-8", "pendingBytes": len(pending),
                  "pendingBase64": base64.b64encode(pending).decode("ascii")}
        payload = raw[:consumed]
    except UnicodeDecodeError:
        consumed, payload = len(raw), raw
        result = {"text": None, "encoding": "binary", "pendingBytes": 0, "pendingBase64": ""}
    return {"logRef": doc["logRef"], "offset": offset, "nextOffset": offset + consumed,
            "eof": offset + consumed == size, "sizeBytes": size,
            "bytesBase64": base64.b64encode(payload).decode("ascii"), **result}
