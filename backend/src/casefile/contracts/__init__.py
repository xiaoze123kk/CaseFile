"""Versioned CaseFile machine contracts and validation helpers."""

from casefile.contracts.validation import (
    CASEFILE_SCHEMA_VERSION,
    LEGACY_CASEFILE_SCHEMA_VERSIONS,
    SUPPORTED_CASEFILE_SCHEMA_VERSIONS,
    ContractValidationError,
    load_casefile_schema,
    public_validation_issues,
    validate_casefile,
)

__all__ = [
    "CASEFILE_SCHEMA_VERSION",
    "LEGACY_CASEFILE_SCHEMA_VERSIONS",
    "SUPPORTED_CASEFILE_SCHEMA_VERSIONS",
    "ContractValidationError",
    "load_casefile_schema",
    "public_validation_issues",
    "validate_casefile",
]
