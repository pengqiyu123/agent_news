"""WeChat MP selector profiles — FAITHFUL COPY from auto-news-studio browser_base.py:36-235.

Every selector is copied verbatim. Do not "improve" them — they are tuned to
the real WeChat MP DOM. When WeChat changes their DOM, add a new selector to
the FRONT of the list (keeping old ones as fallback), matching the old project's
strategy.
"""

from __future__ import annotations

WECHAT_MP_V1: dict[str, list[str]] = {
    "logged_in": [
        ".weui-desktop-account__thumb",
        ".weui-desktop-layout__main",
        ".weui-desktop-side-menu",
    ],
    "new_article": [
        ".new-creation__menu-item:has(.new-creation__menu-title:text-is('文章'))",
        ".new-creation__menu-content:has(.new-creation__menu-title:text-is('文章'))",
        ".new-creation__menu-title:text-is('文章')",
    ],
    "draft_box": [
        "a#menu_10125[href*='action=list_card']",
        "a.weui-desktop-menu__link.menu_report[href*='action=list_card']",
        "a:has-text('草稿箱')",
        "div:has-text('草稿箱')",
        "a[href*='action=list_card']",
        "text=草稿箱",
        "a[href*='draft']",
    ],
    "publish_history": [
        "a#menu_10126[href*='appmsgpublish']",
        "a.weui-desktop-menu__link.menu_report[href*='appmsgpublish']",
        "a:has-text('发表记录')",
        "div:has-text('发表记录')",
        "a[href*='appmsgpublish']",
        "text=发表记录",
    ],
    "content_manage": [
        "span.weui-desktop-menu__link[title='内容管理']",
        "span.weui-desktop-menu__name:has-text('内容管理')",
        "a:has-text('内容管理')",
        "div:has-text('内容管理')",
        "text=内容管理",
    ],
    "title_input": [
        "div.ProseMirror[data-placeholder*='请在这里输入标题']",
        "div.ProseMirror[data-placeholder*='标题']",
        "textarea.js_article_title",
        "input[placeholder*='标题']",
        "textarea[placeholder*='标题']",
    ],
    "author_input": [
        "input.js_author",
        "input[placeholder*='作者']",
    ],
    "digest_input": [
        "textarea.js_desc",
        "textarea[placeholder*='摘要']",
    ],
    "editor": [
        "#edui1_iframeholder .mock-iframe-body .rich_media_content > div.ProseMirror[contenteditable='true']",
        "#edui1_iframeholder .mock-iframe-body .rich_media_content div.ProseMirror[contenteditable='true']",
        ".editor-v-root .mock-iframe-body .rich_media_content > div.ProseMirror[contenteditable='true']",
        "div.ProseMirror:not([data-placeholder*='请在这里输入标题']):not([data-placeholder*='标题'])",
        "div.ProseMirror:not([data-placeholder*='请在这里输入标题']):not([data-placeholder*='标题'])[style*='min-height']",
        ".rich_media_content .ProseMirror:not([data-placeholder*='请在这里输入标题']):not([data-placeholder*='标题'])",
        "div.ProseMirror:has(.editor_content_placeholder)",
        ".rich_media_content [contenteditable='true']",
        ".rich_media_content",
    ],
    "save_draft_button": [
        "button:has-text('保存为草稿')",
        "span:has-text('保存为草稿')",
        "text=保存为草稿",
    ],
    "original_setting": [
        "#js_original",
        ".js_original_apply_cell",
        ".appmsg-editor__setting-group.origined__setting-group",
    ],
    "reward_setting": [
        "#js_reward_setting_area",
        ".reward__setting-group.js_reward_open_cell",
        ".reward__setting-group",
    ],
    "collection_setting": [
        "div.js_article_tags_label",
        "#js_article_tags_area .allow_click_opr",
        "#js_article_tags_area .js_article_tags_content",
        "#js_article_tags_area .lbl_content_desc",
    ],
    "collection_picker_input": [
        "span.weui-desktop-form__input-wrp:has(input.weui-desktop-form__input[placeholder='请选择合集'])",
        "span.weui-desktop-form__input-wrp:has(input[placeholder*='请选择合集'])",
        "input.weui-desktop-form__input[placeholder='请选择合集']",
        "input.weui-desktop-form__input[placeholder*='请选择合集']",
        ".weui-desktop-form__input-wrp input[placeholder*='合集']",
    ],
    "collection_ai_news_option": [
        "li.select-opt-li:has-text('AI新闻')",
        ".select-opt-li:has-text('AI新闻')",
        "li:has-text('AI新闻')",
    ],
    "claim_source_setting": [
        "div.js_claim_source_desc",
        "div.allow_click_opr.js_claim_source_desc",
        "label.claim_source_label_wrapper",
    ],
    "claim_source_personal_option": [
        "label.weui-desktop-form__check-label:has-text('个人观点，仅供参考')",
        ".weui-desktop-form__check-label:has-text('个人观点')",
    ],
    "primary_confirm_button": [
        "button.weui-desktop-btn.weui-desktop-btn_primary:has-text('确定')",
        "button.weui-desktop-btn_primary:has-text('确定')",
        "button:has-text('确定')",
    ],
    "option_confirm_button": [
        "button.weui-desktop-btn.weui-desktop-btn_primary:has-text('确认')",
        "button.weui-desktop-btn_primary:has-text('确认')",
        ".weui-desktop-btn_wrp button.weui-desktop-btn_primary:has-text('确认')",
        "button:has-text('确认')",
        "button.weui-desktop-btn.weui-desktop-btn_primary:has-text('确定')",
        "button.weui-desktop-btn_primary:has-text('确定')",
        "button:has-text('确定')",
    ],
    "cover_button": [
        "div.select-cover__btn.js_cover_btn_area.select-cover__mask",
        ".js_cover_btn_area.select-cover__mask",
        ".js_cover_btn_area",
        "span.js_share_type_none_image:has-text('拖拽或选择封面')",
    ],
    "ai_image_button": [
        "a.pop-opr__button.js_aiImage:has-text('AI 配图')",
        "a.js_aiImage",
        ".js_aiImage",
        "text=AI 配图",
        "span:has-text('AI 配图')",
        "[class*='iconContainer']:has-text('AI 配图')",
        "[class*='mycard-info-text-icon']:has-text('AI 配图')",
    ],
    "ai_image_prompt": [
        "textarea#ai-image-prompt",
        "textarea[name='ai-image-prompt']",
        "textarea[placeholder*='请描述你想要创作的内容']",
    ],
    "ai_image_send_button": [
        "button.send-btn",
        ".send-btn",
    ],
    "ai_image_generated_tip": [
        "p.ai-image__tips:has-text('已为你生成图片')",
        ".ai-image__tips:has-text('已为你生成图片')",
    ],
    "ai_image_use_button": [
        ".ai-image-operation-group .ai-image-op-btn:has-text('使用')",
        ".ai-image-op-btn:has-text('使用')",
        "div:has-text('使用')",
    ],
    "cover_confirm_button": [
        ".weui-desktop-btn_wrp button.weui-desktop-btn_primary:has-text('确认')",
        "button.weui-desktop-btn_primary:has-text('确认')",
        "button:has-text('确认')",
    ],
    "article_publish_button": [
        "#js_send button.mass_send:has-text('发表')",
        "#js_send .send_wording:has-text('发表')",
        "#js_send button.mass_send",
        "button.mass_send:has-text('发表')",
    ],
    "publish_modal_button": [
        ".weui-desktop-popover__wrp .weui-desktop-btn_wrp[slot='target'] button.weui-desktop-btn_primary:has-text('发表')",
        ".weui-desktop-popover__wrp button.weui-desktop-btn_primary:has-text('发表')",
        ".weui-desktop-dialog__wrp button.weui-desktop-btn_primary:has-text('发表')",
        ".weui-dialog button.weui-desktop-btn_primary:has-text('发表')",
        "[role='dialog'] button.weui-desktop-btn_primary:has-text('发表')",
    ],
    "continue_publish_button": [
        "button.weui-desktop-btn_primary:has-text('继续发表')",
        ".weui-desktop-btn_wrp button:has-text('继续发表')",
        "button:has-text('继续发表')",
    ],
    "wechat_verify_qrcode": [
        ".dialog:has-text('微信验证') img.js_qrcode",
        ".safe_check img.js_qrcode",
        "img.js_qrcode[alt='微信二维码']",
        "img.js_qrcode",
    ],
}


def get_selector_profile(version: str = "wechat-mp-v1") -> dict[str, list[str]]:
    """Return the selector dict for a profile version. Falls back to wechat-mp-v1."""
    if version == "wechat-mp-v1":
        return WECHAT_MP_V1
    return WECHAT_MP_V1


def get(key: str, version: str = "wechat-mp-v1") -> list[str]:
    """Shorthand: get one selector list by key."""
    return get_selector_profile(version).get(key, [])
