"""Pure state derivation: the reducers and `build_run_result`.

These carry no LLM and no graph — they are the serialization the output
contract rests on, so their edge branches are exercised directly.
"""

from fie.state import Claim, EngineState, build_run_result, keep_first_error


def test_keep_first_error_keeps_the_first() -> None:
    """First error wins so concurrent branch failures don't clobber it; a
    None existing yields to the new one."""
    assert keep_first_error("first", "second") == "first"
    assert keep_first_error(None, "second") == "second"
    assert keep_first_error(None, None) is None


def test_build_run_result_cites_a_missing_chunk_by_id_alone() -> None:
    """A claim can cite a chunk id that is not in the final evidence (an older
    checkpoint, a dropped chunk). The source is still reported — by id, with no
    provenance fields — rather than omitted."""
    state = EngineState(
        question="q",
        draft_answer="Apple reported revenue growth.",
        claims=[
            Claim(
                id="cl-1",
                text="Apple reported revenue growth",
                claim_type="qualitative",
                status="grounded",
                source_chunk_ids=["ghost"],
            )
        ],
    )
    result = build_run_result("run-missing-chunk", state)

    assert len(result.grounding) == 1
    sources = result.grounding[0].sources
    assert len(sources) == 1
    assert sources[0].chunk_id == "ghost"
    assert not sources[0].doc_id and not sources[0].ticker
