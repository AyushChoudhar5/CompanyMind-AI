import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.documents import Document
from backend.app.core import publication as pub
from backend.app.documents.revisions import PublishRequest, ReviewRequest
from backend.app.rag.impact import compare_question
from backend.app.rag.ingest import load_catalog, load_registered
from backend.tests.test_employee_access import system, sign_in
from backend.tests.test_hr_uploads import admin_system


def draft(client, headers, base='policy', **metadata):
    return client.post(f'/admin/documents/{base}/revisions', headers=headers,
        data={'metadata': json.dumps({'version': '2', **metadata})},
        files={'file': ('revised.txt', b'Revised policy: employees may work remotely one day per week. Manager approval is required.')})


def comparison(question, old, new, client):
    assert {d.metadata['document_id'] for d in old} != {d.metadata['document_id'] for d in new}
    return {'question': question, 'status': 'changed', 'reason': 'Rule changed',
        'current': {'answer': old[0].page_content, 'references': [1]},
        'proposed': {'answer': new[0].page_content, 'references': [1]},
        'current_sources': [], 'proposed_sources': []}


def review(client, headers, identifier):
    client.app.state.revisions.comparison = comparison
    response=client.post(f'/admin/revisions/{identifier}/review', headers=headers, json={'questions':['How many remote days?']})
    assert response.status_code == 202, response.text
    for _ in range(100):
        report=client.get(f'/admin/revisions/{identifier}',headers=headers).json()['review']
        if report['status'] != 'running': break
        time.sleep(.02)
    assert report['status'] == 'complete', report
    return report


def publish(client, headers, identifier, report, **changes):
    return client.post(f'/admin/revisions/{identifier}/publish',headers=headers,
        json={'review_id':report['review_id'], 'acknowledge_review':True, **changes})


@pytest.mark.parametrize('method,path', [('GET','/admin/documents/policy/revisions'), ('POST','/admin/documents/policy/revisions'), ('GET','/admin/revisions/missing'), ('POST','/admin/revisions/missing/review'), ('POST','/admin/revisions/missing/publish')])
def test_revision_endpoints_require_hr(admin_system, method, path):
    client,_,_=admin_system
    assert client.request(method,path).status_code == 401
    assert client.request(method,path,headers=sign_in(client)).status_code == 403


@pytest.mark.parametrize('base,viewer', [('policy','bob'),('alice-offer','alice')])
def test_draft_hidden_then_published_preserves_audience(admin_system,base,viewer):
    client,manager,_=admin_system
    hr=sign_in(client,'hr')
    response=draft(client,hr,base)
    assert response.status_code == 201, response.text
    identifier=response.json()['document_id']
    spec=next(s for s in load_catalog(manager.catalog_path) if s.document_id==identifier)
    assert spec.lifecycle=='draft'
    assert spec.owner_id == ('EMP001' if base=='alice-offer' else None)
    for user in ['alice','bob','hr']:
        headers=sign_in(client,user)
        assert identifier not in client.get('/documents',headers=headers).text
        assert client.get(f'/documents/{identifier}/preview',headers=headers).status_code==404
        assert client.post('/search',headers=headers,json={'question':'Policy?', 'document_id':identifier}).status_code==409
    # Re-ingestion keeps the draft gate rather than exposing a replacement.
    docs=load_registered(spec,manager.data_dir)
    assert all(d.metadata['lifecycle']=='draft' for d in docs)
    report=review(client,hr,identifier)
    assert publish(client,hr,identifier,report,acknowledge_review=False).status_code==422
    assert publish(client,hr,identifier,report).status_code==200
    allowed=sign_in(client,viewer)
    listed={d['document_id'] for d in client.get('/documents',headers=allowed).json()['documents']}
    assert identifier in listed and base not in listed
    assert client.get(f'/documents/{base}/preview',headers=allowed).status_code==404
    if base=='alice-offer':
        assert identifier not in client.get('/documents',headers=sign_in(client,'bob')).text
    assert client.get(f'/admin/revisions/{identifier}',headers=hr).json()['revision']['status']=='published'


def test_future_cutover_has_exactly_one_current_version(admin_system,monkeypatch):
    client,_,_=admin_system
    hr=sign_in(client,'hr'); alice=sign_in(client)
    identifier=draft(client,hr).json()['document_id']
    report=review(client,hr,identifier)
    now=pub.now_epoch(); cutoff=now+3600
    result=publish(client,hr,identifier,report,effective_at=datetime.fromtimestamp(cutoff,timezone.utc).isoformat())
    assert result.status_code==200, result.text
    assert result.json()['revision']['status']=='scheduled'
    for instant,expected,excluded in [(cutoff-1,'policy',identifier),(cutoff,identifier,'policy'),(cutoff+1,identifier,'policy')]:
        monkeypatch.setattr(pub,'now_epoch',lambda:instant)
        ids={d['document_id'] for d in client.get('/documents',headers=alice).json()['documents']}
        assert expected in ids and excluded not in ids


@pytest.mark.parametrize('metadata',[{'version':'1'},{'visibility':'company'},{'owner_id':'EMP002'}])
def test_revision_cannot_relabel_same_version_or_change_owner(admin_system,metadata):
    client,_,_=admin_system
    assert draft(client,sign_in(client,'hr'),'alice-offer',**metadata).status_code==422


def test_publication_requires_fresh_completed_review(admin_system):
    client,manager,_=admin_system; hr=sign_in(client,'hr')
    identifier=draft(client,hr).json()['document_id']
    assert publish(client,hr,identifier,{'review_id':'00000000-0000-0000-0000-000000000000'}).status_code==409
    report=review(client,hr,identifier)
    manager.store._collection.update(ids=['policy'],metadatas=[{'version':'changed-after-review'}])
    assert publish(client,hr,identifier,report).status_code==409


def test_failed_comparison_cannot_publish(admin_system):
    client,_,_=admin_system; hr=sign_in(client,'hr')
    identifier=draft(client,hr).json()['document_id']
    client.app.state.revisions.comparison=Mock(side_effect=RuntimeError('secret-provider-detail'))
    response=client.post(f'/admin/revisions/{identifier}/review',headers=hr,json={'questions':['Any policy changes?']})
    assert response.status_code==202
    for job in list(client.app.state.revisions._jobs.values()): job.result(timeout=5)
    detail=client.get(f'/admin/revisions/{identifier}',headers=hr)
    assert detail.json()['review']['status']=='failed'
    assert 'secret-provider-detail' not in detail.text
    assert publish(client,hr,identifier,response.json()).status_code==409


@pytest.mark.parametrize('questions',[[], ['same question','SAME QUESTION'],['a'],['valid question']*9])
def test_review_question_validation(questions):
    with pytest.raises(ValueError): ReviewRequest(questions=questions)


def test_catalog_write_failure_restores_visible_original(admin_system,monkeypatch):
    client,manager,_=admin_system; hr=sign_in(client,'hr')
    identifier=draft(client,hr).json()['document_id']; report=review(client,hr,identifier)
    monkeypatch.setattr(manager,'_save',Mock(side_effect=OSError('disk failure')))
    assert publish(client,hr,identifier,report).status_code==503
    ids={d['document_id'] for d in client.get('/documents',headers=sign_in(client)).json()['documents']}
    assert 'policy' in ids and identifier not in ids


@pytest.mark.parametrize('reference',[True, '1', 0, 2])
def test_invalid_model_citations_rejected(reference):
    client=Mock(); result={'current':{'answer':'One day','references':[reference]},'proposed':{'answer':'Two days','references':[1]},'status':'changed','reason':'Different allowance'}
    client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(result)))])
    doc=Document(page_content='Policy text',metadata={'source':'policy.txt'})
    with pytest.raises(ValueError): compare_question('How many days?',[doc],[doc],client)


def test_missing_evidence_forces_review_status():
    client=Mock(); result={'current':{'answer':'Unknown','references':[]},'proposed':{'answer':'Two days','references':[1]},'status':'changed','reason':'Different allowance'}
    client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(result)))])
    doc=Document(page_content='Policy text',metadata={'source':'policy.txt'})
    assert compare_question('How many days?',[doc],[doc],client)['status']=='needs_review'


def test_deletion_cannot_break_policy_history(admin_system):
    client,_,_=admin_system; hr=sign_in(client,'hr')
    identifier=draft(client,hr).json()['document_id']
    assert client.delete('/admin/documents/policy',headers=hr).status_code==409
    report=review(client,hr,identifier)
    assert publish(client,hr,identifier,report).status_code==200
    assert client.delete(f'/admin/documents/{identifier}',headers=hr).status_code==409


def test_review_failure_if_retriever_returns_wrong_owner(admin_system,monkeypatch):
    client,manager,_=admin_system; hr=sign_in(client,'hr')
    identifier=draft(client,hr,'alice-offer').json()['document_id']
    compare=Mock(); client.app.state.revisions.comparison=compare
    monkeypatch.setattr(manager.store,'similarity_search',lambda *a,**kw:[Document(page_content='Wrong owner',metadata={'document_id':'alice-offer','visibility':'employee','owner_id':'EMP002'})])
    response=client.post(f'/admin/revisions/{identifier}/review',headers=hr,json={'questions':['What changed here?']})
    assert response.status_code==202
    for job in list(client.app.state.revisions._jobs.values()): job.result(timeout=5)
    assert client.get(f'/admin/revisions/{identifier}',headers=hr).json()['review']['status']=='failed'
    compare.assert_not_called()


@pytest.mark.parametrize('fail_call',[3,4])
def test_partial_index_publish_rolls_back(admin_system,monkeypatch,fail_call):
    client,manager,_=admin_system; hr=sign_in(client,'hr')
    identifier=draft(client,hr).json()['document_id']; report=review(client,hr,identifier)
    collection_type=type(manager.store._collection); update=collection_type.update; calls=0
    def fail_once(self,*args,**kwargs):
        nonlocal calls
        calls+=1
        result=update(self,*args,**kwargs)
        if calls==fail_call: raise RuntimeError('Failure after index mutation')
        return result
    monkeypatch.setattr(collection_type,'update',fail_once)
    assert publish(client,hr,identifier,report).status_code==503
    ids={d['document_id'] for d in client.get('/documents',headers=sign_in(client)).json()['documents']}
    assert 'policy' in ids and identifier not in ids
    assert next(s for s in load_catalog(manager.catalog_path) if s.document_id==identifier).lifecycle=='draft'


def test_restart_marks_unfinished_review_failed(admin_system):
    client,manager,_=admin_system; hr=sign_in(client,'hr')
    identifier=draft(client,hr).json()['document_id']
    client.app.state.revisions._write(manager,identifier,{'status':'running','questions':['Question?'],'results':[]})
    assert client.get(f'/admin/revisions/{identifier}',headers=hr).json()['review']['status']=='failed'
