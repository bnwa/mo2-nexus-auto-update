from typing import Optional, TypeVar

T = TypeVar("T")


def present(value: Optional[T]) -> T:
    """Narrow a PyQt6 return value the stubs type as Optional but Qt never leaves null here."""
    assert value is not None
    return value
