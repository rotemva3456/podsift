"""Offline acceptance checks for imported video speech and source-bound learning."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import video_learning, video_transcription, video_vision, videos
from companion.deps import current_user
from companion.llm import FakeLLM, LLMError, get_llm
from companion.routes.videos import get_video_speech
from companion.server import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ('TRANSCRIPTION_API_BASE_URL', 'COURSE_WATCHER_URL', 'COURSE_WATCHER_PUBLIC_URL',
                'LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'):
        monkeypatch.delenv(key, raising=False)
    app = create_app('http://podfetch.test', tmp_path / 'notes.db', transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[get_video_speech] = lambda: None
    monkeypatch.setattr(videos, 'probe_video', lambda _: {'duration': 70.0, 'has_audio': True})
    with TestClient(app) as c:
        yield c
        queue = getattr(app.state, 'video_queue', None)
    if queue:
        queue.pool.shutdown(wait=True)


def imported(client, content=b'private video', filename='lesson.mp4'):
    response = client.post('/companion/videos', params={'filename': filename}, content=content)
    assert response.status_code == 201, response.text
    return response.json()


def saved_transcript(client, source, count=3):
    doc = {'segments': [{'id': f'v{i+1}', 'start': float(i * 10), 'end': float(i * 10 + 9),
                        'text': f'Explanation {i+1}.'} for i in range(count)], 'digest': 'source-digest',
           'processed_seconds': 70, 'coverage_kind': 'full_audio', 'visual_coverage': 'none',
           'text': 'all speech', 'words': [], 'timed': True}
    videos.persist_json(client.app.state.settings.database, source['id'], 'default', 'transcript_json', doc)
    return doc


def wait_done(client, source):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get(f"/companion/videos/{source['id']}").json()
        if body['status'] not in ('queued', 'running', 'cancelling'):
            return body
        time.sleep(.01)
    raise AssertionError('video job did not finish')


def point(ids=('v1',)):
    return {'points': [{'text': 'A supported explanation.', 'citations': list(ids)}], 'moments': [],
            'caveat': 'Speech only. The screen has not been inspected.'}


def test_app_shutdown_cancels_video_work_and_closes_its_executor(tmp_path, monkeypatch):
    from companion import engine

    monkeypatch.setattr(videos, 'probe_video', lambda _: {'duration': 70.0, 'has_audio': True})
    app = create_app('http://podfetch.test', tmp_path / 'notes.db',
                     transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    started, stopped = threading.Event(), threading.Event()

    def work(cancel, progress):
        started.set()
        if not cancel.wait(5):
            raise AssertionError('App shutdown did not cancel video work')
        stopped.set()
        raise engine.Cancelled('App stopped')

    with TestClient(app) as c:
        source = imported(c)
        c.get('/companion/videos').raise_for_status()
        queue = app.state.video_queue
        queue.start(source['id'], 'summary', work)
        assert started.wait(3)
    assert stopped.wait(3)
    queue.pool.shutdown(wait=True)
    assert not hasattr(app.state, 'video_queue')
    with pytest.raises(RuntimeError, match='shutdown'):
        queue.pool.submit(lambda: None)


def test_import_is_durable_deduplicated_and_never_starts_processing(client):
    first = imported(client, filename='../../lesson.mp4')
    assert first['title'] == 'lesson.mp4' and first['status'] == 'saved'
    assert first['has_transcript'] is False and first['learning'] is None
    assert imported(client)['id'] == first['id']
    assert len(client.get('/companion/videos').json()['videos']) == 1
    media = client.get(first['media_url'])
    assert media.content == b'private video'
    assert 'private' in media.headers['cache-control']
    assert client.get(f"/companion/videos/{first['id']}/media").content == b'private video'
    assert client.get(f"/companion/videos/{first['id']}/media?token=wrong").status_code == 404
    # Reimport repairs a lost original instead of deduplicating back to a
    # permanently broken link; its source identity and read capability survive.
    original = videos.root(client.app.state.settings.database) / first['id'] / first['filename']
    original.unlink()
    assert client.get(first['media_url']).status_code == 404
    assert imported(client)['id'] == first['id']
    assert client.get(first['media_url']).content == b'private video'


def test_other_user_cannot_read_or_process_a_private_video(client):
    source = imported(client)
    client.app.dependency_overrides[current_user] = lambda: 'alice'
    assert client.get('/companion/videos').json()['videos'] == []
    base = f"/companion/videos/{source['id']}"
    assert client.get(base).status_code == 404
    assert client.get(base + '/transcript').status_code == 404
    for suffix in ('transcribe', 'cancel', 'visual-review'):
        assert client.post(base + '/' + suffix).status_code == 404
    assert client.post(base + '/learn', json={'task': 'summary'}).status_code == 404
    other = imported(client)
    assert other['id'] != source['id'] and other['media_url'] != source['media_url']


def test_upload_rejects_empty_oversized_or_wrong_type_and_cleans_files(client, monkeypatch):
    assert client.post('/companion/videos?filename=lesson.exe', content=b'x').status_code == 415
    assert client.post('/companion/videos?filename=lesson.mp4', content=b'').status_code == 422
    monkeypatch.setattr(videos, 'MAX_UPLOAD_BYTES', 4)
    assert client.post('/companion/videos?filename=lesson.mp4', content=b'12345').status_code == 413
    assert list(videos.root(client.app.state.settings.database).iterdir()) == []


def test_provider_unavailable_leaves_original_playable(client):
    source = imported(client)
    assert client.post(f"/companion/videos/{source['id']}/transcribe").status_code == 409
    assert client.get(source['media_url']).content == b'private video'
    assert client.get(f"/companion/videos/{source['id']}").json()['status'] == 'saved'


def test_transcript_pages_cover_every_passage_without_unpaged_text(client):
    source = imported(client)
    saved_transcript(client, source, 17)
    seen, cursor = [], 0
    while cursor is not None:
        response = client.get(f"/companion/videos/{source['id']}/transcript", params={'cursor': cursor, 'limit': 4})
        assert response.status_code == 200
        page = response.json()
        seen.extend(s['id'] for s in page['segments'])
        assert page['text'] == '\n'.join(s['text'] for s in page['segments'])
        assert page['digest'] == 'source-digest' and page['visual_coverage'] == 'none'
        cursor = page['next_cursor']
    assert seen == [f'v{i+1}' for i in range(17)]


def test_learning_runs_only_on_request_and_resolves_source_times(client):
    source = imported(client)
    saved_transcript(client, source)
    llm = FakeLLM([{'points': [], 'moments': [{'start_id': 'v2', 'end_id': 'v3', 'action': 'check_screen',
                                            'title': 'Inspect the example', 'why': 'The speaker refers to a diagram.'}],
                    'caveat': 'Speech only; inspect the screen yourself.'}])
    client.app.dependency_overrides[get_llm] = lambda: llm
    response = client.post(f"/companion/videos/{source['id']}/learn", json={'task': 'watch_plan', 'minutes': 1})
    assert response.status_code == 202
    result = wait_done(client, source)['learning']
    assert result['moments'][0]['start'] == 10 and result['moments'][0]['end'] == 29
    assert result['watch_seconds'] <= 60 and result['visual_coverage'] == 'none'
    assert len(llm.calls) == 1


def test_cancelled_provider_cannot_publish_and_words_survive(client):
    source = imported(client)
    saved_transcript(client, source)
    started, release = threading.Event(), threading.Event()
    def blocked(**_):
        started.set()
        assert release.wait(5)
        return point()
    client.app.dependency_overrides[get_llm] = lambda: FakeLLM([blocked])
    base = f"/companion/videos/{source['id']}"
    assert client.post(base + '/learn', json={'task': 'summary'}).status_code == 202
    assert started.wait(3)
    assert client.post(base + '/learn', json={'task': 'summary'}).status_code == 409
    assert client.post(base + '/cancel').status_code == 200
    release.set()
    final = wait_done(client, source)
    assert final['status'] == 'cancelled' and final['learning'] is None and final['has_transcript']


def test_full_stt_covers_late_speech_and_retry_reuses_paid_chunks(tmp_path, monkeypatch):
    cancel = threading.Event()
    slices = []
    monkeypatch.setattr(video_transcription, 'prepare_chunk', lambda s,t,a,n,c: slices.append((a,n)) or n)
    monkeypatch.setattr(video_transcription.render, 'envelope', lambda *a, **k: [SimpleNamespace(levels=(-20,))])
    class Speech:
        calls = 0
        base_url, speech_model = 'http://fake', 'whisper'
        def transcribe_words(self, _):
            self.calls += 1
            if self.calls == 2:
                raise LLMError('Provider unavailable.')
            duration = 600 if self.calls == 1 else 20
            return {'duration': duration, 'text': 'Important final explanation.',
                    'segments': [{'start': 2, 'end': 8, 'text': 'Important final explanation.'}],
                    'words': [{'start': 2, 'end': 3, 'word': 'Important'}]}
    speech = Speech()
    with pytest.raises(LLMError):
        video_transcription.full_transcript(tmp_path/'video', 620, 'sha', tmp_path/'chunks', speech, cancel, lambda _: None)
    doc = video_transcription.full_transcript(tmp_path/'video', 620, 'sha', tmp_path/'chunks', speech, cancel, lambda _: None)
    assert speech.calls == 3
    assert doc['segments'][-1]['start'] == 602 and doc['segments'][-1]['end'] == 608
    assert doc['processed_seconds'] == 620 and doc['coverage'][-1]['end'] == 620
    assert doc['visual_coverage'] == 'none'


def test_silent_audio_does_not_call_speech_or_invent_words(tmp_path, monkeypatch):
    monkeypatch.setattr(video_transcription, 'prepare_chunk', lambda *a: 20)
    monkeypatch.setattr(video_transcription.render, 'envelope', lambda *a, **k: [SimpleNamespace(levels=(-120,))])
    speech = SimpleNamespace(transcribe_audio=lambda _: pytest.fail('silent audio sent to STT'))
    doc = video_transcription.full_transcript(tmp_path/'video', 20, 'sha', tmp_path/'chunks', speech, threading.Event(), lambda _: None)
    assert doc['text'] == '' and doc['segments'] == [] and doc['processed_seconds'] == 20


@pytest.mark.parametrize('payload', [
    {'duration': 34, 'text': 'hello', 'segments': [{'start': 0, 'end': 1, 'text': 'hello'}]},
    {'text': 'hello', 'segments': [{'start': 5, 'end': 30, 'text': 'hello'}]},
    {'text': 'hello', 'segments': []},
    {'text': 'hello important ending', 'segments': [{'start': 0, 'end': 1, 'text': 'hello'}]},
    {'segments': [{'start': float('nan'), 'end': 1, 'text': 'hello'}]},
])
def test_stt_rejects_stretched_invalid_or_untimed_speech(payload):
    with pytest.raises(LLMError):
        video_transcription._timed(payload, 20)


def test_word_decoder_padding_is_clamped_without_accepting_times_outside_source():
    raw = {'duration': 45, 'text': 'last word', 'segments': [{'start': 42, 'end': 45, 'text': 'last word'}],
           'words': [{'start': 42, 'end': 43, 'word': 'last'}, {'start': 44.5, 'end': 45.5, 'word': 'word'}]}
    segments, words = video_transcription._timed(raw, 45)
    assert segments[-1]['end'] == 45 and words[-1]['end'] == 45
    assert words[-1]['start'] == 44.5
    raw['words'][-1]['end'] = 47
    segments, words = video_transcription._timed(raw, 45)
    assert segments[-1]['end'] == 45 and words == []
    raw['segments'] = []
    with pytest.raises(LLMError):
        video_transcription._timed(raw, 45)


def test_learning_reads_tail_of_long_transcript_and_bounds_each_request():
    segments = [{'id': f'v{i}', 'start': i*10, 'end': i*10+8, 'text': f'Section {i}. ' + 'important '*80} for i in range(11)]
    doc = {'segments': segments, 'digest': 'all', 'processed_seconds': 118}
    def respond(**kw):
        data = json.loads(kw['user'])
        evidence = data.get('speech_evidence')
        if evidence:
            return point((evidence[-1]['id'],))
        return point(tuple(p['citations'][0] for r in data['partial_results'] for p in r['points'])[:8])
    llm = FakeLLM([respond]*20)
    llm.max_input_chars = 3800
    result = video_learning.learn_video(doc, 'lecture', video_learning.LearnRequest(), llm, threading.Event(), lambda _: None)
    evidence_calls = [json.loads(c['user'])['speech_evidence'] for c in llm.calls if 'speech_evidence' in json.loads(c['user'])]
    assert [s['id'] for group in evidence_calls for s in group] == [s['id'] for s in segments]
    assert all(len(call['user']) <= 3800 for call in llm.calls)
    assert 'v10' in result['points'][0]['citations']


def test_learning_rejects_foreign_citations_and_never_clips_a_budget_interval():
    doc = {'segments': [{'id': 'v1', 'start': 4, 'end': 65, 'text': 'Whole explanation.'}], 'digest': 'd', 'processed_seconds': 70}
    with pytest.raises(LLMError, match='outside this video'):
        video_learning.learn_video(doc, 'title', video_learning.LearnRequest(), FakeLLM([point(('foreign',))]), threading.Event(), lambda _: None)
    too_long = {'points': [], 'moments': [{'start_id': 'v1', 'end_id': 'v1', 'action': 'watch', 'title': 'Whole example', 'why': 'Useful explanation.'}], 'caveat': 'Speech only.'}
    with pytest.raises(LLMError, match='budget'):
        video_learning.learn_video(doc, 'title', video_learning.LearnRequest(task='watch_plan', minutes=1), FakeLLM([too_long]), threading.Event(), lambda _: None)


def test_visual_handoff_preserves_times_and_never_runs_vision(tmp_path, monkeypatch):
    monkeypatch.setenv('COURSE_WATCHER_URL', 'http://watcher.test')
    monkeypatch.setenv('COURSE_WATCHER_PUBLIC_URL', 'https://watcher.example')
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'video')
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/upload'):
            assert request.content == b'video'
            return httpx.Response(200, json={'source_id': 9})
        payload = json.loads(request.content)
        assert payload['plan']['intervals'][0]['start_s'] == 42
        assert payload['plan']['intervals'][0]['end_s'] == 63
        assert payload['engine'] == 'orchestrated' and payload['review_mode'] == 'assisted'
        return httpx.Response(200, json={'session_id': 'review-1'})
    video = {'id': 'v', 'filename': 'video.mp4', 'sha256': 'bytes', 'duration': 90}
    learning = {'goal': 'Read the diagram', 'transcript_digest': 'd', 'moments': [
        {'start': 42, 'end': 63, 'action': 'check_screen', 'why': 'Speaker describes arrows.'}]}
    result = video_vision.prepare_review(source, video, learning, transport=httpx.MockTransport(handler))
    assert result['url'] == 'https://watcher.example/?review=review-1#plan'
    assert result['visual_coverage'] == 'none' and len(calls) == 2
    assert all('/run' not in str(c.url) for c in calls)
    assert video_vision.review_request(video, {**learning, 'goal': 'Check a different point'})[3] != result['request_key']
