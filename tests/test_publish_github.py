import base64

import pytest

from scripts import publish_github


def test_snapshot_includes_project_and_excludes_local_files(tmp_path):
    for name in publish_github.ROOT_FILES:
        (tmp_path / name).write_text("public")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "publish_github.py").write_text("safe")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text("safe")
    (tmp_path / "backend" / ".env").write_text("not public")
    (tmp_path / "backend" / "__pycache__").mkdir()
    (tmp_path / "backend" / "__pycache__" / "app.pyc").write_bytes(b"cache")
    (tmp_path / ".env").write_text("not public")

    files = publish_github.snapshot(tmp_path)
    assert files["scripts/publish_github.py"] == b"safe"
    assert files["backend/app.py"] == b"safe"
    assert ".env.example" in files
    assert not any(".env" in name or "__pycache__" in name
                   for name in files if name != ".env.example")


def test_snapshot_refuses_hardcoded_token(tmp_path):
    for name in publish_github.ROOT_FILES:
        (tmp_path / name).write_text("public")
    (tmp_path / "README.md").write_text("ghp_" + "x" * 30)
    with pytest.raises(ValueError, match="Potential credential"):
        publish_github.snapshot(tmp_path)


def test_publish_creates_public_repo_and_verifies_commit(monkeypatch):
    calls = []
    blobs = {}

    def fake_api(token, method, path, data=None):
        assert token == "test-token"
        calls.append((method, path, data))
        if path == "/user":
            return {"login": publish_github.OWNER}
        repo = f"/repos/{publish_github.OWNER}/{publish_github.REPO}"
        if path == repo and method == "GET":
            raise RuntimeError(f"GitHub API returned HTTP 404 for GET {path}")
        if path == "/user/repos":
            assert data["private"] is False and data["auto_init"] is True
            return {"owner": {"login": publish_github.OWNER}, "private": False,
                    "default_branch": "main", "html_url": "https://github.com/example/project"}
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "new-commit" if method == "GET" and
                    any(call[0] == "PATCH" for call in calls) else "initial-commit"}}
        if path.endswith("/git/commits/initial-commit"):
            return {"tree": {"sha": "initial-tree"}}
        if path.endswith("/git/blobs"):
            sha = f"blob-{len(blobs)}"
            blobs[sha] = base64.b64decode(data["content"])
            return {"sha": sha}
        if path.endswith("/git/trees") and method == "POST":
            assert data["base_tree"] == "initial-tree"
            return {"sha": "new-tree"}
        if path.endswith("/git/commits") and method == "POST":
            assert data["parents"] == ["initial-commit"]
            return {"sha": "new-commit"}
        if path.endswith("/git/refs/heads/main") and method == "PATCH":
            assert data == {"sha": "new-commit", "force": False}
            return {}
        if path.endswith("/git/trees/new-tree?recursive=1"):
            tree = next(data for method, path, data in calls
                        if method == "POST" and path.endswith("/git/trees"))
            return {"truncated": False, "tree": tree["tree"]}
        raise AssertionError((method, path))

    monkeypatch.setattr(publish_github, "api", fake_api)
    assert publish_github.publish({"README.md": b"new content"}, "test-token") == (
        "https://github.com/example/project", "main", "new-commit")
    assert list(blobs.values()) == [b"new content"]
