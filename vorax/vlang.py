"""Official V-Lang import surface; legacy T-Lang records remain readable."""
from .tlang import (
    VERSION, LEGACY_VERSION, ID_RE, CHILD_RE, TYPES, OPS, TOOL_SCHEMAS,
    TemplateError, validate_template, parse_document, fingerprint, dump_record,
    execute,
)

__all__ = [
    "VERSION", "LEGACY_VERSION", "ID_RE", "CHILD_RE", "TYPES", "OPS",
    "TOOL_SCHEMAS", "TemplateError", "validate_template", "parse_document",
    "fingerprint", "dump_record", "execute",
]
