"""Versioned descriptor validation with local, immutable meta schemas."""
import json
from functools import lru_cache
from pathlib import Path

import jsonschema


@lru_cache(maxsize=2)
def _validator(kind):
    schema = json.loads((Path(__file__).parent / "schemas" / f"{kind}.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def validate_document(kind, document):
    try:
        _validator(kind).validate(document)
    except jsonschema.ValidationError as exc:
        location = ".".join(map(str, exc.absolute_path)) or "root"
        raise ValueError(f"Invalid {kind} document at {location}: {exc.message}") from exc
