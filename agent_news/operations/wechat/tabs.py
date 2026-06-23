"""WeChat tab recovery operations."""

from __future__ import annotations

from pathlib import Path

from ...browser import BROWSER_MANAGER, default_wechat_channel
from ...models.operation import OperationResult
from ..base import operation
from .cover import _read_cover_preview_state

_CHANNEL = default_wechat_channel()


@operation(
    name="wechat.inspect_tabs",
    category="navigation",
    description="只读：返回当前浏览器标签页 URL、标题、是否 blank、是否编辑页。",
    params={},
)
def inspect_tabs(ctx) -> OperationResult:
    state = BROWSER_MANAGER.observe_tabs()
    return OperationResult.success(message=f"observed {state.get('page_count', 0)} tabs", **state)


@operation(
    name="wechat.focus_editor_tab",
    category="navigation",
    description="聚焦已有 action=edit 编辑页，不新开页面。",
    params={},
)
def focus_editor_tab(ctx) -> OperationResult:
    state = BROWSER_MANAGER.focus_editor_tab()
    if state.get("focused"):
        return OperationResult.success(message="focused editor tab", **state)
    return OperationResult.failure(message=state.get("error") or "editor tab not found", **state)


@operation(
    name="wechat.close_blank_tabs",
    category="navigation",
    description="关闭重复 about:blank 标签，不关闭编辑页。",
    params={},
)
def close_blank_tabs(ctx) -> OperationResult:
    state = BROWSER_MANAGER.close_blank_tabs()
    return OperationResult.success(message=f"closed {state.get('closed_count', 0)} blank tabs", **state)


@operation(
    name="wechat.upload_cover_file",
    category="publish_settings",
    description="上传本地封面图片；找不到上传 input 时明确失败，不伪装成功。",
    params={"file_path": "本地图片路径"},
)
def upload_cover_file(ctx, file_path: str) -> OperationResult:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return OperationResult.failure(message=f"cover file not found: {file_path}")
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return OperationResult.failure(message="cover file must be png/jpg/jpeg/webp")

    def _action(_context, page):
        url = str(getattr(page, "url", "") or "")
        if "action=edit" not in url and "appmsg_edit" not in url:
            return OperationResult.failure(message="当前不在编辑页，无法上传封面", url=url)
        inputs = page.locator("input[type='file']")
        count = inputs.count()
        if count == 0:
            return OperationResult.failure(message="未找到封面上传 input[type=file]", url=url)
        inputs.nth(0).set_input_files(str(path))
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass
        cover_state = _read_cover_preview_state(page)
        if cover_state.get("hasCover"):
            return OperationResult.success(message="uploaded cover file", cover_preview=cover_state, file_path=str(path))
        return OperationResult.failure(message="封面上传后未检测到预览", cover_preview=cover_state, file_path=str(path))

    try:
        return BROWSER_MANAGER.with_session(_CHANNEL, action_fn=_action, reset_on_failure=False)
    except Exception as exc:
        return OperationResult.failure(message=f"upload_cover_file 失败: {type(exc).__name__}: {exc}")

