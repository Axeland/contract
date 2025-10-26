"""Custom exception types for the contract library."""

class ContractError(Exception):
    """Base exception for all errors raised by the contract library."""
    pass


class SchemaError(ContractError):
    """Raised when a contract schema is malformed or invalid."""
    pass


class ValidationError(ContractError):
    """Raised when data fails to validate against a contract."""
    def __init__(self, message, field_path=None):
        self.field_path = field_path if field_path is not None else []
        if self.field_path:
            path_str = '.'.join(str(p) for p in self.field_path)
            full_message = f"Validation failed at '{path_str}': {message}"
        else:
            full_message = message
        super().__init__(full_message)


class TypeMismatchError(ValidationError):
    """Raised when a value's type does not match the expected type in the contract."""
    def __init__(self, expected_type, actual_type, field_path):
        message = f"Expected type '{expected_type.__name__}', but got '{actual_type.__name__}'"
        super().__init__(message, field_path=field_path)


class MissingFieldError(ValidationError):
    """Raised when a required field is missing from the data."""
    def __init__(self, field_name, field_path):
        message = f"Required field '{field_name}' is missing"
        # The path should point to the object that is missing the field.
        super().__init__(message, field_path=field_path)
