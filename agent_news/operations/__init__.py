"""Operations package — the atomic-operation registry.

Importing this package registers all available operations via the @operation
decorator on each module. To add a new operation domain, create a module and
import it here.
"""

from .base import OperationContext, OperationEntry, operation
from .registry import OPERATION_REGISTRY, OperationRegistry

# Import operation modules so their @operation decorators run on package import.
# Each module registers itself into OPERATION_REGISTRY as a side effect.
from . import radar  # noqa: F401  — radar operations (sync/cluster/score/deepdive)
from . import wechat  # noqa: F401  — wechat operations (nav/fill/save/publish)

__all__ = [
    "OPERATION_REGISTRY",
    "OperationContext",
    "OperationEntry",
    "OperationRegistry",
    "operation",
]
