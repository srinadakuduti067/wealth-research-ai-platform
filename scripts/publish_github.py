import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OWNER = "srinadakuduti067"
REPO = "wealth-research-copilot"
DIRECTORIES = ("backend", "data", "db", "frontend", "scripts", "tests")
ROOT_FILES = (".dockerignore", ".env.example", ".gcloudignore", ".gitignore",
              "Dockerfile", "README.md", "docker-compose.yml")
EXCLUDED = {".git", ".venv", ".pytest_cache", "__pycache__", ".DS_Store", ".env"}
TOKEN_PATTERN = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"gh[pousr]_[A-Za-z0-9_]{20,}|"
    rb"AIza[0-9A-Za-z_-]{25,}|"
    rb"sk-[A-Za-z0-9_-]{20,}"
)


def snapshot(root=ROOT):
    paths = [root / name for name in ROOT_FILES]
    paths.extend(path for directory in DIRECTORIES
                 for path in (root / directory).rglob("*") if path.is_file())
    files = {}
    for path in sorted(paths):
        relative = path.relative_to(root)
        if any(part in EXCLUDED or part.startswith(".") for part in relative.parts[1:]):
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Refusing missing or linked file: {relative}")
        if path.suffix in {".pyc", ".pyo", ".pem", ".key"}:
            continue
        data = path.read_bytes()
        if len(data) > 5_000_000:
            raise ValueError(f"File too large for publishing: {relative}")
        if TOKEN_PATTERN.search(data):
            raise ValueError(f"Potential credential in {relative}; not publishing")
        files[relative.as_posix()] = data
    return files


def api(token, method, path, data=None):
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "wealth-research-copilot-publisher",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub API returned HTTP {error.code} for {method} {path}") from None


def publish(files, token):
    user = api(token, "GET", "/user")
    if user["login"].lower() != OWNER.lower():
        raise ValueError(f"GitHub token belongs to {user['login']}, not {OWNER}")
    repo_path = f"/repos/{OWNER}/{REPO}"
    try:
        api(token, "GET", repo_path)
    except RuntimeError as error:
        if "HTTP 404" not in str(error):
            raise
    else:
        raise ValueError(f"Repository already exists: {OWNER}/{REPO}")

    repository = api(token, "POST", "/user/repos", {
        "name": REPO,
        "description": "Synthetic Wealth Research Copilot MVP",
        "private": False,
        "auto_init": True,
    })
    if repository["owner"]["login"].lower() != OWNER.lower() or repository["private"]:
        raise ValueError("Created repository has unexpected ownership or visibility")
    branch = repository["default_branch"]
    for attempt in range(5):
        try:
            parent = api(token, "GET", f"{repo_path}/git/ref/heads/{branch}")["object"]["sha"]
            break
        except RuntimeError as error:
            if "HTTP 404" not in str(error) or attempt == 4:
                raise
            time.sleep(2)
    base_tree = api(token, "GET", f"{repo_path}/git/commits/{parent}")["tree"]["sha"]
    entries = []
    for path, content in files.items():
        blob = api(token, "POST", f"{repo_path}/git/blobs", {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = api(token, "POST", f"{repo_path}/git/trees", {
        "base_tree": base_tree,
        "tree": entries,
    })
    commit = api(token, "POST", f"{repo_path}/git/commits", {
        "message": "Publish synthetic Wealth Research Copilot MVP",
        "tree": tree["sha"],
        "parents": [parent],
    })
    api(token, "PATCH", f"{repo_path}/git/refs/heads/{branch}", {
        "sha": commit["sha"],
        "force": False,
    })
    actual = api(token, "GET", f"{repo_path}/git/trees/{tree['sha']}?recursive=1")
    expected = {entry["path"]: entry["sha"] for entry in entries}
    uploaded = {entry["path"]: entry["sha"] for entry in actual["tree"] if entry["type"] == "blob"}
    if actual["truncated"] or uploaded != expected:
        raise RuntimeError("Repository created, but remote file verification did not match")
    head = api(token, "GET", f"{repo_path}/git/ref/heads/{branch}")["object"]["sha"]
    if head != commit["sha"]:
        raise RuntimeError("Repository created, but remote commit verification did not match")
    return repository["html_url"], branch, commit["sha"]


def main():
    parser = argparse.ArgumentParser(description="Publish a safe project snapshot to GitHub.")
    parser.add_argument("--apply", action="store_true", help="Create the public repository and upload")
    args = parser.parse_args()
    try:
        files = snapshot()
        print(f"Public repository: {OWNER}/{REPO}")
        print(f"Snapshot: {len(files)} files ({sum(map(len, files.values()))} bytes)")
        for path in files:
            print(path)
        if not args.apply:
            print("Dry-run only. Set GITHUB_TOKEN and pass --apply to publish.")
            return 0
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise ValueError("GITHUB_TOKEN is required; do not put tokens in files or command arguments")
        url, branch, commit = publish(files, token)
        print(f"Published {url} on {branch} at {commit}")
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Publish failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
