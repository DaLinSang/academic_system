"""Structural smoke tests for the Codex-style Streamlit layout."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.ui.streamlit_app import _split_answer_sections

APP_PATH = Path(__file__).parents[1] / "app" / "ui" / "streamlit_app.py"


def test_chat_layout_exposes_session_list_right_rail_and_input() -> None:
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()

    assert not app.exception
    assert any("新建会话" in button.label for button in app.button)
    assert any(button.label == "设置" for button in app.button)
    assert any(button.label == "长期记忆" for button in app.button)
    assert any(expander.label.startswith("文档状态") for expander in app.expander)
    assert any(
        expander.label.startswith("Claim Verification")
        for expander in app.expander
    )
    assert any(expander.label.startswith("运行信息") for expander in app.expander)
    assert len(app.chat_input) == 1
    assert len(app.get("file_uploader")) == 1
    assert not any(selectbox.label == "当前会话" for selectbox in app.selectbox)
    assert not any(expander.label == "设置" for expander in app.expander)
    assert not any(expander.label == "长期记忆" for expander in app.expander)


def test_opening_memory_dialog_does_not_reset_real_runtime() -> None:
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()

    next(button for button in app.button if button.label == "设置").click().run()
    app.radio[0].set_value("Real").run()
    assert app.session_state["runtime"] == "Real"

    next(button for button in app.button if button.label == "长期记忆").click().run()
    assert app.session_state["runtime"] == "Real"

    next(button for button in app.button if button.label == "设置").click().run()
    assert app.radio[0].value == "Real"


def test_claim_divider_splits_before_missing_evidence_section() -> None:
    body, feedback = _split_answer_sections(
        "## 开放性问题：可靠性\n\n正常回答内容 [1]。\n\n"
        "**缺少证据的部分**：\n现有证据未提供 RAG 内容。\n\n"
        "⚠️ 以下内容与已检索证据不一致或缺少支持，请谨慎采信："
    )

    assert body.endswith("正常回答内容 [1]。")
    assert feedback.startswith("**缺少证据的部分**：")
    assert "⚠️ 以下内容" in feedback


def test_claim_divider_keeps_normal_answer_intact_without_feedback() -> None:
    answer = "普通回答。\n\n继续说明。"

    assert _split_answer_sections(answer) == (answer, "")


def test_claim_divider_recognises_numbered_missing_information_heading() -> None:
    body, feedback = _split_answer_sections(
        "4. **共同点与差异分析**\n\n正常回答。\n\n"
        "5. **缺少的信息**\n\n现有证据不足。"
    )

    assert body.endswith("正常回答。")
    assert feedback.startswith("5. **缺少的信息**")


def test_claim_divider_splits_before_insufficient_material_section() -> None:
    body, feedback = _split_answer_sections(
        "5. 不同记忆组件的关系\n\n正常回答内容 [6]。\n\n"
        "**现有资料不足以回答的部分**：\n\n"
        "- 证据未提供该讲义的完整大纲。\n"
        "- 证据未涉及其他核心模块。"
    )

    assert body.endswith("正常回答内容 [6]。")
    assert feedback.startswith("**现有资料不足以回答的部分**：")
    assert "完整大纲" in feedback


def test_claim_divider_does_not_split_inline_insufficient_material_text() -> None:
    answer = "根据现有资料不足以回答全部问题，但可以说明已知内容。"

    assert _split_answer_sections(answer) == (answer, "")
