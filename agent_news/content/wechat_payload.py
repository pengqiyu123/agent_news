"""Convert stored articles into WeChat editor payloads."""

from __future__ import annotations

import re

from ..models.article import Article


_VISUAL_KEYWORDS = (
    ("iPhone", "一台银色 iPhone 放在极简办公桌上，屏幕关闭，旁边有细小电路板和柔和自然光"),
    ("苹果", "一台银色智能手机和白色无线耳机放在浅灰办公桌上，写实产品摄影"),
    ("OpenAI", "一枚简洁的金属 AI 芯片放在黑色玻璃桌面上，旁边有蓝色状态灯"),
    ("ChatGPT", "一枚发光的 AI 芯片和一台打开的笔记本电脑放在深色办公桌上"),
    ("Anthropic", "一台打开的笔记本电脑显示抽象对话界面，旁边放着咖啡和便签"),
    ("Claude", "一台极简笔记本电脑和一叠设计草图放在木质桌面上，暖色自然光"),
    ("芯片", "一颗高端处理器芯片放在主板中央，微距写实摄影，冷色科技光"),
    ("半导体", "一片晶圆和一颗处理器芯片放在洁净实验台上，写实产品摄影"),
    ("机器人", "一个小型桌面机器人放在实验室工作台上，背景有模糊的工具架"),
    ("自动驾驶", "一辆白色电动车模型放在城市道路沙盘上，旁边有传感器模块"),
    ("融资", "一只钢笔、一份未写字的合同和一台计算器放在办公桌上，商务写实摄影"),
    ("模型", "一组抽象神经网络节点投影在玻璃板上，前景是一台轻薄笔记本"),
)


def _compact_title_terms(title: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", title or "").strip()
    terms = [item for item in cleaned.split() if item]
    return "、".join(terms[:3])


def default_cover_prompt(article: Article) -> str:
    title = (article.title or "").strip()
    for keyword, prompt in _VISUAL_KEYWORDS:
        if keyword.lower() in title.lower():
            return f"{prompt}，不包含任何文字、logo、水印或海报排版"
    terms = _compact_title_terms(title)
    if terms:
        return f"一件现代科技产品和办公桌面物件组成的写实摄影画面，暗示{terms}主题，不包含任何文字、logo、水印或海报排版"
    return "一件现代科技产品放在干净办公桌上的写实产品摄影图，不包含任何文字、logo、水印或海报排版"


def prepare_wechat_payload(
    article: Article,
    *,
    cover_prompt: str = "",
    quality_report: dict | None = None,
    enforce_quality_gate: bool = True,
) -> dict:
    prompt = (cover_prompt or "").strip() or default_cover_prompt(article)
    missing_required = []
    if not (article.title or "").strip():
        missing_required.append("title")
    if not (article.author or "").strip():
        missing_required.append("author")
    if not (article.body_markdown or "").strip():
        missing_required.append("body_markdown")
    quality_passed = bool((quality_report or {}).get("passed", False))
    quality_blocked = enforce_quality_gate and quality_report is not None and not quality_passed
    suggested_steps = []
    if not missing_required and not quality_blocked:
        suggested_steps = [
            {
                "op": "wechat.fill_editor_required",
                "params": {
                    "title": article.title,
                    "author": article.author,
                    "body_markdown": article.body_markdown,
                },
            },
            {"op": "wechat.fill_digest", "params": {"text": article.digest}},
            {"op": "wechat.generate_ai_cover", "params": {"prompt": prompt}},
        ]

    return {
        "article_id": article.id,
        "title": article.title,
        "author": article.author,
        "digest": article.digest,
        "body_markdown": article.body_markdown,
        "cover_prompt": prompt,
        "missing_required": missing_required,
        "quality_report": quality_report,
        "quality_gate_passed": quality_passed if quality_report is not None else None,
        "quality_gate_enforced": enforce_quality_gate,
        "ready_for_wechat_fill": not missing_required and not quality_blocked,
        "suggested_steps": suggested_steps,
    }
