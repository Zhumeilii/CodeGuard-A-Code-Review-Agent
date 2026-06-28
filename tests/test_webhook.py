#!/usr/bin/env python3
"""Webhook route regression tests."""
import asyncio
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations import webhook

pytestmark = pytest.mark.skipif(
    webhook.FastAPI is None,
    reason="FastAPI dependencies are not available",
)


def test_ping_event_returns_pong():
    from fastapi.testclient import TestClient

    client = TestClient(webhook.create_app(github_token="dummy-token"))
    response = client.post(
        "/webhook/github",
        headers={"X-GitHub-Event": "ping"},
        json={"zen": "Keep it logically awesome."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pong"
    assert response.json()["webhook_version"]


def test_placeholder_github_token_is_not_usable():
    assert not webhook.is_usable_github_token("")
    assert not webhook.is_usable_github_token("your_github_token_here")
    assert webhook.is_usable_github_token("dummy-token")


def test_ignored_pull_request_action_does_not_start_review():
    from fastapi.testclient import TestClient

    client = TestClient(webhook.create_app(github_token="dummy-token"))
    response = client.post(
        "/webhook/github",
        headers={"X-GitHub-Event": "pull_request"},
        json={"action": "closed"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "action": "closed"}


def test_push_event_rejects_placeholder_github_token():
    from fastapi.testclient import TestClient

    client = TestClient(webhook.create_app(github_token="your_github_token_here"))
    response = client.post(
        "/webhook/github",
        headers={"X-GitHub-Event": "push"},
        json={
            "ref": "refs/heads/feature/review",
            "repository": {"full_name": "owner/repo"},
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "GITHUB_TOKEN not configured"


def test_reviewable_pull_request_action_is_accepted(monkeypatch):
    from fastapi.testclient import TestClient

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    client = TestClient(webhook.create_app(github_token="dummy-token"))
    response = client.post(
        "/webhook/github",
        headers={"X-GitHub-Event": "pull_request"},
        json={
            "action": "ready_for_review",
            "pull_request": {"number": 123},
            "repository": {"full_name": "owner/repo"},
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["pr"] == 123
    assert response.json()["repo"] == "owner/repo"
    assert response.json()["webhook_version"]
    assert len(created_tasks) == 1


def test_push_event_is_accepted_for_branch(monkeypatch):
    from fastapi.testclient import TestClient

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    client = TestClient(webhook.create_app(github_token="dummy-token"))
    response = client.post(
        "/webhook/github",
        headers={"X-GitHub-Event": "push"},
        json={
            "ref": "refs/heads/feature/review",
            "repository": {"full_name": "owner/repo"},
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["event"] == "push"
    assert response.json()["repo"] == "owner/repo"
    assert response.json()["branch"] == "feature/review"
    assert response.json()["webhook_version"]
    assert len(created_tasks) == 1


def test_root_path_accepts_github_webhook_push(monkeypatch):
    from fastapi.testclient import TestClient

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)

    client = TestClient(webhook.create_app(github_token="dummy-token"))
    response = client.post(
        "/",
        headers={"X-GitHub-Event": "push"},
        json={
            "ref": "refs/heads/feature/review",
            "repository": {"full_name": "owner/repo"},
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["event"] == "push"
    assert response.json()["repo"] == "owner/repo"
    assert response.json()["branch"] == "feature/review"
    assert len(created_tasks) == 1
