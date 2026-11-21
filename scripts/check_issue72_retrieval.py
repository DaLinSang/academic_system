"""Read-only regression of the Issue #72 demo corpus; optional real LLM answer.

uv run python scripts/check_issue72_retrieval.py [--real-answer]
Requires the original poster, lecture and RAG paper already indexed. Does not
import PDFs, modify memories, or persist session data. No keys are printed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.hybrid_retriever import HybridRetriever  # noqa: E402

POSTER = "HKU_TEC_x_Plug_and_Play.pdf"
COURSE = "comp7608_lecture_agent.pdf"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-answer', action='store_true', help='Calls configured cloud model')
    args = parser.parse_args()
    r = HybridRetriever()
    questions = [
        f'只根据 {POSTER} 第1页，Language字段是什么？',
        'LECTURE 6 的日期、时间和地点是什么？',
        'lecture6的language是什么',
        'lecture6的语言是什么',
        'COMP7608B Lecture6讲了什么',
    ]
    for q in questions:
        expected = COURSE if q.startswith('COMP7608B') else POSTER
        found = r.retrieve(q, top_k=3)
        assert found and {e.file_name for e in found} == {expected}, (q, found)
        if expected == POSTER:
            assert len(found) == 1 and found[0].page_number == 1
            assert 'Language / English' in found[0].text
        assert not r.last_warning, r.last_warning
        print('PASS', q, [(e.file_name, e.page_number) for e in found])
    assert not r.retrieve('lecture6讲了什么')
    assert r.last_clarification and POSTER in r.last_clarification
    print('PASS ambiguity:', r.last_clarification)
    if args.real_answer:
        from app.agent.factory import build_stage1_orchestrator
        from app.core.mocks import MockLLM
        from app.core.types import ConversationTurn
        orch = build_stage1_orchestrator(enable_web_search=False)
        assert not isinstance(orch.llm, MockLLM), 'Configure a real LLM first; Mock is not acceptance.'
        history = []
        for q in (questions[1], questions[2]):
            state = orch.run(q, history=history, enable_memory=False)
            assert state.evidence and {e.file_name for e in state.evidence} == {POSTER}
            assert not state.errors, state.errors
            assert state.citations and all(c.page_number == 1 for c in state.citations)
            if q == questions[2]:
                assert 'English' in state.final_answer or '英语' in state.final_answer
                assert '[1]' in state.final_answer
            print('REAL', q, '\n', state.final_answer)
            history.append(ConversationTurn(question=q, answer=state.final_answer,
                                            turn_index=len(history)))


if __name__ == '__main__':
    main()
