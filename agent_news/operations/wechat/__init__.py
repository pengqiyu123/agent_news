"""WeChat operations package — all WeChat publish steps as atomic operations.

Importing this package registers every operation via the @operation decorator.
Each submodule is imported so its operations register into OPERATION_REGISTRY.
"""

from . import navigation, editor, save_publish, drafts, cover, publish_settings, history

__all__ = ["navigation", "editor", "save_publish", "drafts", "cover", "publish_settings", "history"]
