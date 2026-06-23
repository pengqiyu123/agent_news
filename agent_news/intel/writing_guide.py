"""Writing guide — the house style the external AI must follow.

The old project baked this as a 120-line f-string in content/briefing.py and
attached it to every deep dive. Here it's a plain function returning the guide
text, so it's tunable without touching extraction logic. The agent reads it
from the deep-dive record before authoring.
"""

from __future__ import annotations


def build_article_writing_guide() -> str:
    """Return the editorial style guide for WeChat public-account articles.

    Threaded into every EventDeepDive so the AI always has the rules it was
    given when authoring — recoverable from the artifact for audit.
    """
    return """# 公众号文章写作指南

你拿到的深挖记录包含事实、引文、时间线和来源链接。请把它写成一篇真正可发布的公众号文章，而不是素材拼接。

## 内容形式（你来判断）
- **长文（800-1000字）**：单一重大事件，多来源、事实充分、有分析空间。
- **短讯合集（5条）**：当天 5 条科技要闻串成一篇完整文章，每条 2-4 句说清"发生了什么/为什么值得看/还不确定什么"。开头 1 句点题，用"首先/然后/接下来/再说/最后"自然串联，结尾 1 段收束趋势。
- **不写**：证据弱、信息旧、与近期重复的话题。

## 结构要求
- 标题用 `#`，正文用 `##` 分节（长文）。
- 导语 1-2 句讲清核心；结尾回扣意义，不要空喊口号。
- 短讯合集不要裸编号（`## 1.`），不要附来源链接列表。

## 事实纪律
- 数字、日期、价格、人名、产品能力必须来自素材，不要新增。
- "可能/预计/测试中"不能写成已经发生。
- 引文用 `>` 引用，必须是真实出处。
- 不确定的地方明确标注，不要硬编故事。

## 风格
- 像编辑在直接讲给读者听，口语自然，不夸张。
- 段落节奏要有变化，避免重复句式和 AI 味填充词。
- 用具体数字代替"很多""大幅"这种模糊词。

## 禁止
- 不要保留 `核心事实`、`这意味着什么`、`还不确定什么` 这种后台栏目名。
- 不要裸 URL。
- 不要把结构化素材稿直接当成稿——那是给你看的，不是给读者看的。
"""
