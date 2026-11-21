from types import SimpleNamespace

import pytest

from alfred.memory import consolidate, review_queue


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(history_dir=tmp_path), path=lambda value: value)


def test_failed_batch_resumes_saved_drafts_and_only_commits_its_turns(config, monkeypatch):
    ref = review_queue.enqueue(config, "cli", "偏好中文", "收到")
    generated = []
    applied = []
    writes = set()

    def generate(config, *, transcripts, sources):
        assert sources == {ref: "偏好中文"}
        generated.append(transcripts)
        return {"lessons": [{"lesson": "测试"}]}

    def apply(config, drafts, *, batch_id):
        writes.add(batch_id)
        applied.append(drafts)
        if len(applied) == 1:
            review_queue.enqueue(config, "web", "悬停参数", "已记下")
            raise OSError("interrupted after write")

    monkeypatch.setattr(consolidate, "generate_drafts", generate)
    monkeypatch.setattr(consolidate, "apply_unattended", apply)
    with pytest.raises(OSError):
        review_queue.run(config, force=True)
    assert review_queue.status(config)["error"] == "OSError"
    assert review_queue.run(config)
    assert len(generated) == len(writes) == 1
    assert applied[0] == applied[1]
    assert review_queue.status(config) == {"pending": 1, "batch": None, "error": None}
    assert review_queue.originals(config, "", source_ref=ref) == [(ref, "偏好中文", "收到")]
    assert not review_queue.originals(config, "偏好中文", session_id="web")
    assert len(review_queue.originals(config, "悬停参数")) == 1


def test_review_publication_is_idempotent(config):
    review_queue.save_review(config, "batch", {"drafts": {"corrections": []}})
    review_queue.save_review(config, "batch", {"drafts": {"corrections": []}})
    assert review_queue.reviews(config) == [("batch", {"drafts": {"corrections": []}})]
    review_queue.dismiss_review(config, "batch")
    review_queue.save_review(config, "batch", {"drafts": {"corrections": []}})
    assert review_queue.reviews(config) == []


def test_fact_requires_verbatim_user_evidence_and_correction_stays_pending(config, monkeypatch):
    import json

    correction = {"text": "用户改用工具 B", "ref": "ref1", "quote": "现在我用 B",
                  "replaces": "用户用工具 A"}
    fake = {"text": "用户已购买 C", "ref": "ref1", "quote": "没有说过的话"}
    fact = {"text": "用户喜欢中文", "ref": "ref1", "quote": "请用中文"}
    output = json.dumps({"memory_entries": [correction, fake, fact]}, ensure_ascii=False)
    monkeypatch.setattr(consolidate, "build_model", lambda *_: None)
    monkeypatch.setattr(consolidate, "MemoryBlocks", lambda _: SimpleNamespace(read=lambda _: ""))
    monkeypatch.setattr(consolidate, "Agent", lambda *args, **kwargs: SimpleNamespace(
        run_sync=lambda *args, **kwargs: SimpleNamespace(output=output)
    ))
    monkeypatch.setattr(consolidate.longterm, "list_all", lambda _: [])
    config.models = SimpleNamespace(memory_write="test-only")
    drafts = consolidate.generate_drafts(config, transcripts="transcript",
                                         sources={"ref1": "现在我用 B，请用中文"})
    assert drafts["corrections"] == [correction]
    assert drafts["memory_entries"] == ["用户喜欢中文"]
    assert drafts["fact_sources"]["用户喜欢中文"]["source_quote"] == "请用中文"
    monkeypatch.setattr(consolidate.longterm, "add_fact", lambda *_args, **kwargs: pytest.fail("not approved"))
    assert consolidate.apply_drafts(config, {"corrections": [correction]}, confirm=lambda _: False) == []


def test_confirmed_correction_writes_new_fact_before_removing_old(config, monkeypatch):
    calls = []

    def add(config, text, *, exact_only, metadata):
        assert exact_only and metadata["source_ref"] == "ref1"
        calls.append(("add", text))
        return {"status": "added"}

    def delete(config, memory_id):
        calls.append(("delete", memory_id))
        return True

    monkeypatch.setattr(consolidate.longterm, "add_fact", add)
    monkeypatch.setattr(consolidate.longterm, "delete", delete)
    monkeypatch.setattr(consolidate.longterm, "list_all", lambda _: [
        {"id": "old-id", "memory": "旧事实"},
        {"id": "similar-id", "memory": "同事的旧事实"},
    ])
    consolidate.apply_drafts(config, {"corrections": [
        {"text": "新事实", "replaces": "旧事实", "ref": "ref1", "quote": "现在改了"},
    ]}, confirm=lambda _: True)
    assert calls == [("add", "新事实"), ("delete", "old-id")]


def test_unattended_retry_reuses_episode_key_and_does_not_duplicate_review(config, monkeypatch):
    from alfred.memory import episodic

    episodes = {}
    attempts = []

    def save(config, episode):
        attempts.append(episode.episode_id)
        episodes[episode.episode_id] = episode.result

    monkeypatch.setattr(episodic, "save_episode", save)
    drafts = {
        "episodes": [{"situation": "问题", "result": "已解决"}],
        "stale_memories": ["旧记忆"],
    }
    consolidate.apply_unattended(config, drafts, batch_id="batch")
    consolidate.apply_unattended(config, drafts, batch_id="batch")
    assert attempts == ["batch-0", "batch-0"]
    assert episodes == {"batch-0": "已解决"}
    assert len(review_queue.reviews(config)) == 1
