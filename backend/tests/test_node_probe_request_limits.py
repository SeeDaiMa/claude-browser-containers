"""大节点列表的受控请求上限；不启动检测容器。"""
from unittest.mock import Mock

import pytest

import manager_core as core
import node_probe
from test_node_latency import client as client, library as library


def node_ids():
    return ['n' + f'{index:020x}' for index in range(5000)]


@pytest.mark.parametrize('action,kind', [('test', 'test-nodes'), ('quality', 'check-node-quality')])
def test_five_thousand_node_ids_fit_the_probe_route(client, library, monkeypatch, action, kind):
    identities = node_ids()

    def select(subscription_id, requested):
        assert subscription_id == library.id and requested == identities
        return [{'id': identity} for identity in identities]

    def enqueue(selected_kind, payload):
        assert selected_kind == kind
        assert payload == {'subscription_id': library.id, 'node_ids': identities}
        return {'id': 'fixture-task', 'state': 'queued'}

    monkeypatch.setattr(node_probe, 'select_nodes', select)
    monkeypatch.setattr(core, 'enqueue', enqueue)
    response = client.post(f'/api/subscriptions/{library.id}/nodes/{action}',
                           headers={'X-CBC-Request': '1'}, json={'node_ids': identities})
    assert response.status_code == 200
    assert response.json()['id'] == 'fixture-task'


@pytest.mark.parametrize('action', ['test', 'quality'])
def test_large_selection_still_validates_node_ids(client, library, action):
    response = client.post(f'/api/subscriptions/{library.id}/nodes/{action}',
                           headers={'X-CBC-Request': '1'}, json={'node_ids': node_ids()})
    assert response.status_code == 400
    assert '所选节点已变化' in response.json()['error']


@pytest.mark.parametrize('action', ['test', 'quality'])
def test_probe_body_above_256_kib_is_rejected_before_queueing(client, library, monkeypatch, action):
    enqueue = Mock(side_effect=AssertionError('Oversized body must not enqueue'))
    monkeypatch.setattr(core, 'enqueue', enqueue)
    response = client.post(f'/api/subscriptions/{library.id}/nodes/{action}',
                           headers={'X-CBC-Request': '1'}, json={'padding': 'x' * (256 * 1024)})
    assert response.status_code == 413
    enqueue.assert_not_called()


def test_other_mutation_routes_keep_the_16_kib_limit(client):
    response = client.patch('/api/instances/fixture', headers={'X-CBC-Request': '1'},
                            json={'display_name': 'x' * 20000})
    assert response.status_code == 413
