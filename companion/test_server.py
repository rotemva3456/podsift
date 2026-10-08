import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from companion.server import create_app

EPISODE = '307f40e8-92f1-44a0-b58b-fbf0e84951d6'


def fixture(tmp_path: Path, *, changed=False):
    library = tmp_path / 'library' / 'sample'
    library.mkdir(parents=True)
    (library / '_feed.json').write_text(json.dumps({'feed': 'https://example.test/feed', 'episodes': [
        {'key': 'one', 'title': 'A source episode', 'duration': 120, 'audio': 'https://example.test/source.mp3',
         'words_file': 'one.txt', 'guid': 'one'}]}))
    (library / 'one.txt').write_text('A subnet divides a network. A prefix defines its size.')
    (library / 'one.timed.json').write_text(json.dumps({'duration':120,'segments':[
        {'start':0,'end':20,'text':'A subnet divides a network.'},
        {'start':20,'end':45,'text':'A prefix defines its size.'}]}))

    def request(req):
        if '/api/v1/episodes/' in req.url.path:
            return httpx.Response(200, json={'podcastEpisode': {'id':EPISODE,'episode_id':EPISODE,
                'name':'A source episode','url':'https://example.test/changed.mp3' if changed else 'https://example.test/source.mp3',
                'total_time':120}})
        return httpx.Response(404)
    transport = httpx.MockTransport(request)
    db = tmp_path / 'notes.db'
    app = create_app('http://podfetch.test',db,library.parent,transport)
    return TestClient(app), db, library.parent, transport


def test_source_timing_and_gap_are_preserved(tmp_path):
    client, *_ = fixture(tmp_path)
    response=client.get(f'/companion/episodes/{EPISODE}/transcript')
    assert response.status_code==200
    assert [(s['start'],s['end']) for s in response.json()['segments']]==[(0,20),(20,45)]
    assert response.json()['media_verified'] is False


def test_changed_media_url_never_reuses_old_transcript(tmp_path):
    client, *_ = fixture(tmp_path,changed=True)
    result=client.get(f'/companion/episodes/{EPISODE}/transcript').json()
    assert result['segments']==[]
    assert result['text']==''


def test_notes_persist_with_source_and_idempotent_retry(tmp_path):
    client,db,library,transport=fixture(tmp_path)
    note={'id':str(uuid4()),'episode_id':EPISODE,'position':30,'text':'Remember the prefix.'}
    assert client.post('/companion/notes',json=note).status_code==201
    assert client.post('/companion/notes',json=note).status_code==201
    restarted=TestClient(create_app('http://podfetch.test',db,library,transport))
    result=restarted.get(f'/companion/notes?episode_id={EPISODE}').json()
    assert len(result)==1
    assert result[0]['position']==30
    assert result[0]['title']=='A source episode'
    assert client.post('/companion/notes',json={**note,'text':'Different note'}).status_code==409


def test_invalid_timestamps_and_empty_notes_are_rejected(tmp_path):
    client,*_=fixture(tmp_path)
    note={'id':str(uuid4()),'episode_id':EPISODE,'position':30,'text':'A note'}
    for change in [{'position':-1},{'position':121},{'text':'   '}]:
        assert client.post('/companion/notes',json={**note,**change}).status_code==422
    assert client.get('/companion/notes').json()==[]


def test_changed_duration_rejects_saved_timing(tmp_path):
    client,db,library,transport=fixture(tmp_path)
    manifest=library/'sample/_feed.json'
    data=json.loads(manifest.read_text());data['episodes'][0]['duration']=200
    manifest.write_text(json.dumps(data))
    client=TestClient(create_app('http://podfetch.test',db,library,transport))
    assert client.get(f'/companion/episodes/{EPISODE}/transcript').status_code==409


def test_backend_failure_is_readable_and_does_not_save(tmp_path):
    def fail(request):return httpx.Response(503)
    client=TestClient(create_app('http://podfetch.test',tmp_path/'notes.db',transport=httpx.MockTransport(fail)))
    note={'id':str(uuid4()),'episode_id':EPISODE,'position':30,'text':'A note'}
    response=client.post('/companion/notes',json=note)
    assert response.status_code==503
    assert 'unavailable' in response.json()['detail']
    # When a login can't be checked while PodFetch is down, reads are refused too; the file shows nothing was saved.
    assert client.get('/companion/notes').status_code==503
    assert sqlite3.connect(tmp_path/'notes.db').execute('SELECT count(*) FROM notes').fetchone()==(0,)
