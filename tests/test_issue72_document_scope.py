"""Issue #72: source correctness matters more than filling a top-k budget."""
import pytest

from app.agent.answer_generator import LLMAnswerGenerator
from app.agent.factory import build_mock_orchestrator
from app.core.errors import RetrievalError
from app.core.types import AgentState, ConversationTurn, DocumentChunk
from app.retrieval.bm25_retriever import BM25Retriever
from app.retrieval.hybrid_retriever import HybridRetriever


class Offline:
    def embed(self, text):
        raise RetrievalError("offline")


def chunk(key, name, text, page=1):
    return DocumentChunk(chunk_id=key, document_id=name, file_name=name,
                         page_number=page, text=text)


@pytest.fixture
def retriever():
    r = HybridRetriever(embedder=Offline())
    r._bm25 = BM25Retriever([
        chunk('poster', 'poster.pdf', 'LECTURE 6\nDate / 19 NOV 2026\nLanguage / English\nVenue / Theatre'),
        chunk('course', 'course.pdf', 'COMP7608B · Lecture 6\nAgent Skill & Memory'),
        chunk('refs', 'rag_paper.pdf', 'Lecture 6 Language English language language', 14),
        chunk('other', 'other.pdf', 'LECTURE 60\nLanguage / French'),
    ])
    r._bm25_loaded = True
    return r


@pytest.mark.parametrize('query', [
    'lecture6的language是什么', 'LECTURE 06 的语言是什么？',
    'What is the language of Lecture 6?', 'lecture6的日期是什么',
    '只根据 poster.pdf 第1页，Language字段是什么？',
])
def test_ocr_field_beats_bibliography_even_without_embeddings(retriever, query):
    evidence = retriever.retrieve(query, top_k=3)
    assert [(e.file_name, e.page_number) for e in evidence] == [('poster.pdf', 1)]
    assert 'Language / English' in evidence[0].text
    assert retriever.last_clarification is None


def test_explicit_filename_overrides_field_in_other_document(retriever):
    assert {e.file_name for e in retriever.retrieve('course.pdf lecture6 language')} == {'course.pdf'}


def test_course_code_is_not_confused_with_poster(retriever):
    assert {e.file_name for e in retriever.retrieve('COMP7608B Lecture6讲什么')} == {'course.pdf'}


def test_ambiguity_requires_clarification_and_resets_on_next_request(retriever):
    assert retriever.retrieve('lecture6讲什么') == []
    assert 'poster.pdf' in retriever.last_clarification
    assert 'course.pdf' in retriever.last_clarification
    assert {e.file_name for e in retriever.retrieve('other.pdf Language')} == {'other.pdf'}
    assert retriever.last_clarification is None
    assert retriever.retrieve('lecture6讲什么') == []


def test_same_fields_in_two_titles_must_not_choose_arbitrarily(retriever):
    retriever._bm25._replace_document_chunks([
        chunk('other', 'other.pdf', 'LECTURE 6\nLanguage / French')])
    assert retriever.retrieve('lecture6 language') == []
    assert retriever.last_clarification


@pytest.mark.parametrize('q', ['lecture99 language', 'absent.pdf language'])
def test_missing_source_never_substitutes_paper(retriever, q):
    assert retriever.retrieve(q) == []
    assert retriever.last_clarification


def test_multi_turn_no_shared_focus_between_sessions(retriever):
    orch = build_mock_orchestrator()
    orch.retriever = retriever
    first = orch.run('lecture6的日期是什么', session_id='poster', enable_routing=False,
                     enable_memory=False, enable_verification=False)
    second = orch.run('lecture6的language是什么', session_id='poster', enable_routing=False,
                      enable_memory=False, enable_verification=False,
                      history=[ConversationTurn(question=first.question,
                                                answer=first.final_answer, turn_index=0)])
    assert [e.file_name for e in second.evidence] == ['poster.pdf']
    other = orch.run('COMP7608B lecture6讲什么', session_id='course', enable_routing=False,
                     enable_memory=False, enable_verification=False)
    assert {e.file_name for e in other.evidence} == {'course.pdf'}


def test_clarification_reaches_answer_without_llm_or_web_search(retriever):
    class MustNotCall:
        def generate(self, *args, **kwargs):
            raise AssertionError('Clarification must not need LLM')
        def search(self, *args, **kwargs):
            pytest.fail('Ambiguous uploaded file is not a web query')
    orch = build_mock_orchestrator()
    orch.retriever = retriever
    orch.answer_generator = LLMAnswerGenerator(MustNotCall())
    orch.web_search_tool = MustNotCall()
    state = orch.run('lecture6讲什么', enable_routing=False, enable_memory=False)
    assert '请在问题中指定 PDF 文件名' in state.final_answer
    assert not state.evidence
    assert not state.errors
    assert state.tool_results[0].metadata['clarification']


def test_planner_subquery_keeps_numbered_title_and_course_code():
    from app.agent.orchestrator import Orchestrator
    q = Orchestrator._enrich_query('language', AgentState(question='COMP7608B Lecture6 language'))
    assert 'Lecture6' in q and 'COMP7608B' in q
