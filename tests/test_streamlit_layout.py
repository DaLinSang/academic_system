"""Structural smoke tests for the Issue #91 Streamlit layout."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parents[1] / "app" / "ui" / "streamlit_app.py"


def test_chat_layout_exposes_session_list_right_rail_and_input() -> None:
    app = AppTest.from_file(APP_PATH, default_timeout=20).run()

    assert not app.exception
    assert any("新建会话" in button.label for button in app.button)
    assert any("管理当前会话" in expander.label for expander in app.expander)
    assert any(expander.label == "运行设置" for expander in app.expander)
    assert any(expander.label.startswith("文档状态") for expander in app.expander)
    assert any(expander.label == "长期记忆" for expander in app.expander)
    assert any(
        expander.label.startswith("Claim Verification")
        for expander in app.expander
    )
    assert any(expander.label.startswith("运行信息") for expander in app.expander)
    assert len(app.chat_input) == 1
    assert len(app.get("file_uploader")) == 1
    assert not any(selectbox.label == "当前会话" for selectbox in app.selectbox)
