"""Web API 测试：REST 端点。"""

import pytest
from fastapi.testclient import TestClient

from alfred.config import Config
from alfred.server.app import create_app


def _make_config(tmp_path):
    """创建最小化测试配置。"""
    return Config(
        providers={
            "test": {
                "type": "openai_compat",
                "base_url": "https://api.example.com",
                "env_key": "",
                "models": ["test-model", "test-model-2"],
            }
        },
        models={"chat": "test:test-model", "memory_write": "test:test-model"},
        memory={"dir": str(tmp_path / "memory")},
        paths={"history_dir": str(tmp_path / "history")},
    )


@pytest.fixture
def client(tmp_path):
    """创建测试客户端。"""
    import os
    import yaml

    # 写入测试配置到临时文件
    config = _make_config(tmp_path)
    config_path = tmp_path / "test_config.yaml"
    config_data = {
        "providers": {
            "test": {
                "type": "openai_compat",
                "base_url": "https://api.example.com",
                "env_key": "",
                "models": ["test-model", "test-model-2"],
            }
        },
        "models": {"chat": "test:test-model", "memory_write": "test:test-model"},
        "memory": {"dir": str(tmp_path / "memory")},
        "paths": {"history_dir": str(tmp_path / "history")},
    }
    config_path.write_text(yaml.dump(config_data), encoding="utf-8")

    app = create_app(str(config_path))
    return TestClient(app)


class TestSessionAPI:
    """测试会话管理 API。"""

    def test_list_sessions_empty(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_create_session(self, client):
        resp = client.post("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert len(data["id"]) > 0

    def test_create_and_list(self, client):
        # 创建会话
        resp = client.post("/api/sessions")
        session_id = resp.json()["id"]

        # 列出会话
        resp = client.get("/api/sessions")
        data = resp.json()
        ids = [s["id"] for s in data]
        assert session_id in ids

    def test_delete_session(self, client):
        # 创建会话
        resp = client.post("/api/sessions")
        session_id = resp.json()["id"]

        # 删除会话
        resp = client.delete(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent_session(self, client):
        resp = client.delete("/api/sessions/nonexistent")
        assert resp.status_code == 404

    def test_set_session_title(self, client):
        resp = client.post("/api/sessions")
        session_id = resp.json()["id"]

        resp = client.patch(
            f"/api/sessions/{session_id}/title",
            json={"title": "测试会话"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_set_session_title_empty(self, client):
        resp = client.post("/api/sessions")
        session_id = resp.json()["id"]

        resp = client.patch(
            f"/api/sessions/{session_id}/title",
            json={"title": ""},
        )
        assert resp.status_code == 400

    def test_get_messages_nonexistent(self, client):
        resp = client.get("/api/sessions/nonexistent/messages")
        assert resp.status_code == 404


class TestMemoryAPI:
    """测试记忆块 API。"""

    def test_get_human_block(self, client):
        resp = client.get("/api/memory/human")
        assert resp.status_code == 200
        data = resp.json()
        assert data["block"] == "human"
        assert "content" in data
        assert "char_count" in data
        assert "char_limit" in data

    def test_get_persona_block(self, client):
        resp = client.get("/api/memory/persona")
        assert resp.status_code == 200
        assert resp.json()["block"] == "persona"

    def test_get_lessons(self, client):
        resp = client.get("/api/memory/lessons")
        assert resp.status_code == 200
        assert resp.json()["block"] == "lessons"

    def test_get_invalid_block(self, client):
        resp = client.get("/api/memory/invalid")
        assert resp.status_code == 400

    def test_update_human_block(self, client):
        resp = client.put(
            "/api/memory/human",
            json={"content": "# 测试用户画像\n- 名字：测试", "reason": "测试更新"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestStatusAPI:
    """测试状态 API。"""

    def test_status(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "chat_model" in data
        assert "memory_write_model" in data
        assert "embed_model" in data
        assert "memory_layer" in data

    def test_models(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        # 每个模型应有 ref, name, provider, type
        for m in data:
            assert "ref" in m
            assert "name" in m
            assert "provider" in m

    def test_set_model(self, client):
        resp = client.patch("/api/model", json={"model": "test:test-model-2"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_set_invalid_model(self, client):
        resp = client.patch("/api/model", json={"model": "unknown:unknown-model"})
        assert resp.status_code == 400

    def test_set_empty_model(self, client):
        resp = client.patch("/api/model", json={"model": ""})
        assert resp.status_code == 400


class TestSkillsAPI:
    """测试技能 API。"""

    def test_list_skills(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # 每个技能应有 name, description, source
        for s in data:
            assert "name" in s
            assert "description" in s
            assert "source" in s


class TestWhoamiAPI:
    """测试 whoami API。"""

    def test_whoami(self, client):
        resp = client.get("/api/whoami")
        assert resp.status_code == 200
        data = resp.json()
        assert "chat_model" in data
        assert "memory_blocks" in data


class TestStaticFiles:
    """测试静态文件。"""

    def test_index(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_style_css(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")

    def test_app_js(self, client):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "")
