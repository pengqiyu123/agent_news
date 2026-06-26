from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _client():
    from agent_news.main import app

    return TestClient(app)


def _upsert_deep_dive(
    *,
    dive_id: str,
    event_id: str = "evt-quality",
    fact_count: int = 5,
    success_count: int = 2,
    status: str = "ready",
):
    from agent_news.db.intel_repository import get_intel_repository
    from agent_news.models.intel import DeepDiveSourceItem, EventDeepDive

    now = datetime.now(timezone.utc).isoformat()
    sources = [
        DeepDiveSourceItem(
            source_key=f"source-{i}",
            source_name=f"Source {i}",
            link=f"https://example.com/{i}",
            title=f"Source title {i}",
            fetch_status="success",
            extract_status="success",
            cleaned_full_text=f"Fact text {i}",
            word_count=800,
        )
        for i in range(success_count)
    ]
    dive = EventDeepDive(
        id=dive_id,
        event_id=event_id,
        status=status,
        started_at=now,
        finished_at=now,
        attempted_count=success_count,
        success_count=success_count,
        failed_count=0,
        sources=sources,
        full_text_sources=sources,
        facts=[f"事实 {i}: OpenAI 在测试中披露了明确变化。" for i in range(fact_count)],
        quotes=["原文引用：testing quote"],
        worthiness={"worth_to_brief": True, "reason": "素材完整"},
    )
    return get_intel_repository().upsert_deep_dive(dive)


def _long_article_body() -> str:
    paragraph = (
        "OpenAI 这次更新不是换一个入口名称，而是把开发者每天要处理的上下文、调用成本和工具链重新摆到台面上。"
        "素材显示，多个来源都提到模型能力和产品路径正在一起变化，这会直接影响团队评估 AI 工具的方式。"
        "对普通用户来说，真正值得看的不是参数本身，而是这些能力什么时候会进入搜索、办公和编程工具。"
    )
    return "# OpenAI 新变化开始影响 AI 工作台\n\n" + "\n\n".join([paragraph] * 9)


def _digest_body() -> str:
    item = (
        "这条消息的重点不是单个参数变化，而是它可能影响用户选择工具的方式。"
        "对开发者来说，它可能改变工具链评估顺序；对普通用户来说，真正变化会体现在搜索、办公和设备入口里。"
        "目前仍要等官方确认更多落地细节，所以不能把测试中能力写成已经普及。"
    )
    return (
        "# 今日5条科技要闻｜AI 工具继续抢入口\n\n"
        f"今天直接看 5 条科技要闻，它们都指向同一个变化：AI 正在从聊天窗口进入更具体的工作场景。\n\n"
        f"首先是 OpenAI 的工具更新，{item}\n\n"
        f"然后是 Anthropic 的产品动作，{item}\n\n"
        f"接下来是芯片公司的新进展，{item}\n\n"
        f"再说智能硬件的新尝试，{item}\n\n"
        f"最后是开发者生态的调整，{item}\n\n"
        "这 5 条共同说明，AI 新闻已经不只是模型能力竞赛，也开始进入成本、设备、入口和开发流程的重新分配。"
    )


def test_article_review_quality_blocks_trae_style_short_single_event_article():
    client = _client()
    _upsert_deep_dive(dive_id="dive-low-quality", fact_count=2, success_count=1, status="ready")
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "诺贝尔得主 John Jumper 离开 DeepMind 加入 Anthropic",
            "author": "AgentNews",
            "digest": "John Jumper 加入 Anthropic。",
            "body_markdown": "John Jumper 离开 DeepMind 加入 Anthropic。这件事值得关注。",
            "material_id": "dive-low-quality",
        }
    })
    assert resp.status_code == 200
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.review_quality/execute", json={
        "params": {"article_id": article_id}
    })
    item = resp.json()["item"]
    assert item["status"] == "failed"
    report = item["state"]["quality_report"]
    assert report["passed"] is False
    assert any("素材不足" in issue for issue in report["issues"])
    assert any("长文正文过短" in issue for issue in report["issues"])

    resp = client.post("/api/operations/article.prepare_wechat_payload/execute", json={
        "params": {"article_id": article_id}
    })
    item = resp.json()["item"]
    assert item["status"] == "failed"
    assert item["state"]["ready_for_wechat_fill"] is False
    assert item["state"]["suggested_steps"] == []
    assert item["state"]["suggested_next_operation"] == "article.review_quality"


def test_article_prepare_wechat_payload_allows_quality_override_for_manual_exception():
    client = _client()
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "人工例外短稿",
            "author": "AgentNews",
            "body_markdown": "这是一篇人工确认的短稿。",
        }
    })
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.prepare_wechat_payload/execute", json={
        "params": {"article_id": article_id, "override_quality_gate": True}
    })
    item = resp.json()["item"]
    assert item["status"] == "ok"
    assert item["state"]["quality_gate_enforced"] is False
    assert item["state"]["ready_for_wechat_fill"] is True


def test_article_prepare_wechat_payload_passes_for_ready_long_article():
    client = _client()
    _upsert_deep_dive(dive_id="dive-ready-quality", fact_count=6, success_count=2, status="ready")
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "OpenAI 新变化开始影响 AI 工作台",
            "author": "AgentNews",
            "digest": "多来源显示，AI 工具正在从模型参数竞争转向工作流竞争。",
            "body_markdown": _long_article_body(),
            "material_id": "dive-ready-quality",
        }
    })
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.prepare_wechat_payload/execute", json={
        "params": {"article_id": article_id}
    })
    item = resp.json()["item"]
    assert item["status"] == "ok", item["message"]
    state = item["state"]
    assert state["ready_for_wechat_fill"] is True
    assert state["quality_gate_passed"] is True
    assert state["quality_report"]["passed"] is True
    assert state["suggested_steps"][0]["op"] == "wechat.fill_editor_required"


def test_article_review_quality_includes_content_strategy_fit():
    from agent_news.content.publish_performance import build_publish_metrics_analysis
    from agent_news.db import get_repository

    client = _client()
    _upsert_deep_dive(dive_id="dive-strategy-fit", fact_count=6, success_count=2, status="ready")
    analysis = build_publish_metrics_analysis(
        [
            {
                "title": "三星2nm翻车、苹果涨价、美国缺电：AI正在掏空你的钱包和电网",
                "url": "https://mp.weixin.qq.com/s/strategy-fit",
                "published_at": "2026-06-26 20:00",
                "read_count": 134,
                "share_count": 3,
            }
        ],
        snapshot_at="2026-06-26T12:00:00+00:00",
    )
    get_repository().record_publish_task(
        operation_name="wechat.analyze_publish_metrics",
        status="success",
        message="snapshot",
        params={"state": {"analysis": analysis}},
    )
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "OpenAI成本砍半：开发者账单开始重新计算",
            "author": "AgentNews",
            "digest": "OpenAI 芯片进展可能改变推理成本。",
            "body_markdown": _long_article_body(),
            "material_id": "dive-strategy-fit",
        }
    })
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.review_quality/execute", json={
        "params": {"article_id": article_id}
    })
    item = resp.json()["item"]
    assert item["ok"]
    fit = item["state"]["content_strategy_fit"]
    assert fit["label"] in ("partial", "strong")
    assert "成本" in fit["matched_impact_keywords"]


def test_five_item_digest_accepts_five_ready_materials_without_override():
    client = _client()
    material_ids = []
    for index in range(5):
        dive_id = f"dive-digest-ready-{index}"
        _upsert_deep_dive(dive_id=dive_id, event_id=f"evt-digest-{index}", fact_count=1, success_count=1)
        material_ids.append(dive_id)

    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "今日5条科技要闻｜AI 工具继续抢入口",
            "author": "AgentNews",
            "digest": "5 条科技要闻串联 AI 工具、芯片和硬件变化。",
            "body_markdown": _digest_body(),
            "material_id": ",".join(material_ids),
        }
    })
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.prepare_wechat_payload/execute", json={
        "params": {"article_id": article_id}
    })
    item = resp.json()["item"]
    assert item["status"] == "ok", item["message"]
    report = item["state"]["quality_report"]
    assert report["content_form"] == "five_item_digest"
    assert report["material_ready"] is True
    assert report["metrics"]["material_count"] == 5
    assert report["metrics"]["ready_material_count"] == 5
    assert report["metrics"]["digest_transition_count"] == 5


def test_five_item_digest_blocks_when_material_count_is_less_than_five():
    client = _client()
    material_ids = []
    for index in range(4):
        dive_id = f"dive-digest-short-{index}"
        _upsert_deep_dive(dive_id=dive_id, event_id=f"evt-digest-short-{index}", fact_count=2, success_count=1)
        material_ids.append(dive_id)

    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "今日5条科技要闻｜AI 工具继续抢入口",
            "author": "AgentNews",
            "body_markdown": _digest_body(),
            "material_id": ",".join(material_ids),
        }
    })
    article_id = resp.json()["item"]["state"]["article_id"]

    resp = client.post("/api/operations/article.review_quality/execute", json={
        "params": {"article_id": article_id}
    })
    item = resp.json()["item"]
    assert item["status"] == "failed"
    report = item["state"]["quality_report"]
    assert report["metrics"]["material_count"] == 4
    assert any("至少 5 个 ready deep dive" in issue for issue in report["issues"])


def test_default_cover_prompt_uses_concrete_visual_object():
    client = _client()
    _upsert_deep_dive(dive_id="dive-cover-openai", fact_count=6, success_count=2)
    resp = client.post("/api/operations/article.create/execute", json={
        "params": {
            "title": "OpenAI 新变化开始影响 AI 工作台",
            "author": "AgentNews",
            "body_markdown": _long_article_body(),
            "material_id": "dive-cover-openai",
        }
    })
    article_id = resp.json()["item"]["state"]["article_id"]
    resp = client.post("/api/operations/article.prepare_wechat_payload/execute", json={
        "params": {"article_id": article_id}
    })
    prompt = resp.json()["item"]["state"]["cover_prompt"]
    assert any(token in prompt for token in ("AI 芯片", "iPhone", "笔记本电脑", "办公桌", "实验台", "计算器"))
    assert "文字" in prompt


def test_partial_deep_dive_does_not_suggest_article_create():
    from agent_news.intel.review import review_deep_dive_state

    dive = _upsert_deep_dive(
        dive_id="dive-partial-suggestion",
        event_id="evt-partial-suggestion",
        fact_count=2,
        success_count=1,
        status="ready",
    )
    state = review_deep_dive_state(dive)
    assert state["writing_readiness"] == "partial"
    assert state["suggested_next_operation"] == "radar.deep_dive_event"


def test_review_deep_dive_state_exposes_writing_guide_for_agent_authors():
    from agent_news.intel.review import review_deep_dive_state

    dive = _upsert_deep_dive(
        dive_id="dive-review-guide",
        event_id="evt-review-guide",
        fact_count=5,
        success_count=2,
        status="ready",
    )

    state = review_deep_dive_state(dive)
    guide = state["article_writing_guide"]
    assert "公众号文章写作指南" in guide
    assert "标题策略" in guide
    assert "前 14 字" in guide
    assert "最终只保留 1 个定稿标题" in guide
    assert "article.title" in guide
    assert "不要向用户抛标题选择题" in guide
