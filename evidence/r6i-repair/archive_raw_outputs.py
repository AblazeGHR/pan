"""Preserve raw output bytes before normalizing review-only trailing space."""
import hashlib
import json
from pathlib import Path
import zipfile

OUT = Path(__file__).resolve().parent
targets = sorted(path for path in OUT.iterdir() if path.suffix in {".log", ".xml", ".txt"})
record = []
# An interrupted earlier pass may already contain original bytes for a prefix.
# Preserve those entries and normalize bytes directly: Windows child output can
# contain CP936 text alongside UTF-8, which must not be replaced or discarded.
previous = {}
if (OUT / "raw-output.zip").exists():
    with zipfile.ZipFile(OUT / "raw-output.zip") as prior:
        previous = {name: prior.read(name) for name in prior.namelist()}
with zipfile.ZipFile(OUT / "raw-output.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in targets:
        original = previous.get(path.name, path.read_bytes())
        archive.writestr(path.name, original)
        lines = original.split(b"\n")
        normalized = b"\n".join(line.rstrip(b"\r \t") for line in lines).rstrip(b"\n") + (b"\n" if original else b"")
        if normalized != original:
            path.write_bytes(normalized)
        record.append({"path":path.name,"rawSha256":hashlib.sha256(original).hexdigest(),
            "normalizedSha256":hashlib.sha256(normalized).hexdigest(),"changed":normalized!=original})
(OUT / "artifact-whitespace.json").write_text(json.dumps({"archive":"raw-output.zip","files":record},indent=2)+"\n",encoding="utf-8")
print(json.dumps({"rawFilesArchived":len(targets),"normalizedFiles":sum(row["changed"] for row in record)}))
