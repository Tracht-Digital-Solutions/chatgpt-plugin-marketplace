"""Collect public, released Agent Plugins without executing their source code."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[0-9A-Za-z.-]+)?\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_FILE = 8 * 1024 * 1024
MAX_PLUGIN = 32 * 1024 * 1024
NOTICES = {"LICENSE": "UPSTREAM_LICENSE", "NOTICE": "UPSTREAM_NOTICE", "THIRD_PARTY_NOTICES.md": "UPSTREAM_THIRD_PARTY_NOTICES.md"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def safe_path(value, allow_root=False):
    require(isinstance(value, str) and bool(value), "Empty path")
    if value == "." and allow_root:
        return value
    path = value.removeprefix("./")
    require(not path.startswith("/") and "\\" not in path and ":" not in path, f"Unsafe path: {value}")
    require(all(p not in ("", ".", "..") for p in path.split("/")), f"Unsafe path: {value}")
    require(not any(ord(c) < 32 for c in path), f"Control character in path: {value}")
    require(all(not p.endswith((" ", ".")) for p in path.split("/")), f"Unsafe path: {value}")
    require(not any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]} for p in path.split("/")), f"Reserved path: {value}")
    return path


class GitHub:
    def __init__(self, token=None):
        self.token = token

    def get(self, path):
        require(path.startswith("/") and not path.startswith("//"), "Invalid API path")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tracht-digital-plugin-marketplace"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request("https://api.github.com" + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read(48 * 1024 * 1024 + 1)
            require(len(data) <= 48 * 1024 * 1024, "API response too large")
            return json.loads(data)
        except urllib.error.HTTPError as error:
            raise ValueError(f"GitHub API returned {error.code} for {path}; check releases, access and rate limits") from None


def config_sources(config, api):
    require(NAME.fullmatch(config.get("name", "")), "Invalid marketplace name")
    require(isinstance(config.get("displayName"), str) and config["displayName"].strip(), "Missing displayName")
    sources = list(config.get("sources", []))
    discovery = config.get("discovery", {})
    topic = discovery.get("topic", "chatgpt-plugin")
    for owner in discovery.get("owners", []):
        require(re.fullmatch(r"[A-Za-z0-9-]+", owner), "Invalid discovery owner")
        page = 1
        while True:
            repos = api.get(f"/users/{owner}/repos?type=owner&sort=full_name&per_page=100&page={page}")
            require(isinstance(repos, list), "Invalid repository list")
            for repo in repos:
                if not repo.get("private") and not repo.get("archived") and not repo.get("fork") and topic in repo.get("topics", []):
                    sources.append({"repository": repo["full_name"], "path": discovery.get("path", "plugin")})
            if len(repos) < 100:
                break
            page += 1
    unique = {}
    for source in sources:
        repository = source.get("repository", "")
        require(REPO.fullmatch(repository), "Invalid repository name")
        path = safe_path(source.get("path", "plugin"), allow_root=True)
        key = (repository.lower(), path)
        normalized = {"repository": repository, "path": path}
        if "tag" in source:
            require(re.fullmatch(r"v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", source["tag"]), "Invalid stable release tag")
            normalized["tag"] = source["tag"]
        if key in unique:
            # An explicit source (first) wins over topic discovery.
            continue
        unique[key] = normalized
    for repository in config.get("exclude", []):
        unique = {key: s for key, s in unique.items() if key[0] != repository.lower()}
    require(bool(unique), "No plugin sources configured or discovered")
    return sorted(unique.values(), key=lambda s: (s["repository"].lower(), s["path"]))


def read_blob(api, repository, item):
    require(item["type"] == "blob" and item.get("mode") in ("100644", "100755"), "Symlinks, submodules and special files are not supported")
    require(0 <= item.get("size", MAX_FILE + 1) <= MAX_FILE, "File exceeds size limit")
    require(SHA.fullmatch(item.get("sha", "")), "Invalid blob SHA")
    blob = api.get(f"/repos/{repository}/git/blobs/{item['sha']}")
    require(blob.get("encoding") == "base64", "Expected a base64 Git blob")
    content = base64.b64decode("".join(blob["content"].split()), validate=True)
    require(len(content) == item["size"] and len(content) <= MAX_FILE, "Blob size mismatch")
    digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    require(digest == item["sha"], "Git blob integrity check failed")
    return content


def validate_plugin(files):
    require("plugin.json" in files, "Portable plugin.json is required at the configured source path")
    manifest = json.loads(files["plugin.json"])
    require(NAME.fullmatch(manifest.get("name", "")) and len(manifest["name"]) <= 64, "Invalid plugin name")
    require(SEMVER.fullmatch(manifest.get("version", "")), "Expected a stable semantic plugin version")
    require(isinstance(manifest.get("description"), str) and manifest["description"].strip(), "Missing plugin description")
    interface = manifest.get("extensions", {}).get("com.openai", {}).get("interface", {})
    require(isinstance(interface.get("category"), str) and interface["category"].strip(), "Missing plugin category")
    require(len(interface.get("shortDescription", "")) <= 30, "Plugin subtitle exceeds 30 characters")
    for field in ("logo", "logoDark", "composerIcon", "composerIconDark"):
        if field in interface:
            require(safe_path(interface[field]) in files, f"Missing local asset: {field}")
    if ".codex-plugin/plugin.json" in files:
        overlay = json.loads(files[".codex-plugin/plugin.json"])
        require(all(overlay.get(k) == manifest[k] for k in ("name", "version")), "Compatibility manifest identity mismatch")
    for name, content in files.items():
        safe_path(name)
        require(not any(p in {".git", "node_modules", ".venv", "__pycache__"} for p in PurePosixPath(name).parts), "Dependency or Git metadata in plugin")
        require(PurePosixPath(name).name != ".env" and not PurePosixPath(name).name.endswith((".pem", ".key")), "Potential secret file in plugin")
        if name.endswith("SKILL.md"):
            text = content.decode("utf-8")
            require(text.startswith("---\n") or text.startswith("---\r\n"), "Skill frontmatter missing")
        if name in ("mcp.json", ".mcp.json"):
            require(isinstance(json.loads(content).get("mcpServers"), dict), "Invalid MCP configuration")
    return manifest, interface


def collect_plugin(source, api):
    repository, path = source["repository"], source["path"]
    metadata = api.get(f"/repos/{repository}")
    require(metadata.get("private") is False and metadata.get("visibility", "public") == "public", f"Refusing to publish a private repository: {repository}")
    release_path = "tags/" + urllib.parse.quote(source["tag"], safe="") if source.get("tag") else "latest"
    release = api.get(f"/repos/{repository}/releases/{release_path}")
    require(not release.get("draft") and not release.get("prerelease"), "Only published stable releases are supported")
    tag = release.get("tag_name", "")
    require(SEMVER.fullmatch(tag.removeprefix("v")), "Expected a stable semantic release tag")
    commit = api.get(f"/repos/{repository}/commits/{urllib.parse.quote(tag, safe='')}")
    sha = commit.get("sha", "")
    require(SHA.fullmatch(sha), "Invalid release commit")
    tree = api.get(f"/repos/{repository}/git/trees/{sha}?recursive=1")
    require(tree.get("truncated") is False, "Truncated Git tree; refusing an incomplete plugin")
    prefix = "" if path == "." else path + "/"
    items = [item for item in tree["tree"] if item["path"].startswith(prefix) and item["type"] != "tree"]
    require(0 < len(items) <= 1000, "Empty plugin or too many files")
    require(sum(item.get("size", MAX_PLUGIN + 1) for item in items) <= MAX_PLUGIN, "Plugin exceeds size limit")
    files = {}
    folded = set()
    executables = []
    for item in items:
        relative = safe_path(item["path"][len(prefix):])
        require(relative.casefold() not in folded, "Duplicate or case-colliding path")
        folded.add(relative.casefold())
        files[relative] = read_blob(api, repository, item)
        if item['mode'] == '100755':
            executables.append(relative)
    for upstream, target in NOTICES.items():
        item = next((item for item in tree["tree"] if item["path"] == upstream), None)
        if item and prefix:
            require(target.casefold() not in folded, "Upstream notice filename collision")
            files[target] = read_blob(api, repository, item)
    require(sum(len(content) for content in files.values()) <= MAX_PLUGIN, 'Plugin with notices exceeds size limit')
    manifest, interface = validate_plugin(files)
    require(manifest["version"] == tag.removeprefix("v"), "Release tag and plugin manifest version differ")
    record = {"name": manifest["name"], "version": manifest["version"], "repository": repository, "path": path, "tag": tag, "commit": sha,
              "executables": sorted(executables),
              "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}}
    entry = {"name": manifest["name"], "source": {"source": "local", "path": f"./plugins/{manifest['name']}"},
             "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": interface["category"]}
    return entry, record, files


def collect(config, api):
    entries, records, output = [], [], {}
    names = set()
    for source in config_sources(config, api):
        entry, record, files = collect_plugin(source, api)
        require(record["name"] not in names, f"Duplicate plugin name: {record['name']}")
        names.add(record["name"])
        entries.append(entry)
        records.append(record)
        output.update({f"plugins/{record['name']}/{name}": content for name, content in files.items()})
    pairs = sorted(zip(entries, records), key=lambda pair: pair[0]["name"])
    entries, records = map(list, zip(*pairs))
    output[".agents/plugins/marketplace.json"] = json_bytes({"name": config["name"], "interface": {"displayName": config["displayName"]}, "plugins": entries})
    output["catalog.lock.json"] = json_bytes({"format": 1, "plugins": records})
    lines = ["# Plugin-Katalog", "", "Automatisch aus veröffentlichten stabilen Releases erzeugt. Eigene MCP-Server können zusätzliche Einrichtung erfordern.", "", "| Plugin | Version | Herkunft |", "| --- | --- | --- |"]
    for record in records:
        lines.append(f"| {record['name']} | {record['version']} | [{record['repository']} @ {record['commit'][:12]}](https://github.com/{record['repository']}/tree/{record['commit']}) |")
    output["CATALOG.md"] = ("\n".join(lines) + "\n").encode()
    return output


def install(root, output):
    # All downloads and validations finish before changing any generated file.
    # GitHub publishes the complete result in one commit after offline verification.
    with tempfile.TemporaryDirectory(prefix=".sync-", dir=root) as temporary:
        stage = Path(temporary)
        for name, content in output.items():
            target = stage / safe_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        lock = json.loads(output['catalog.lock.json'])
        for record in lock['plugins']:
            for relative in record.get('executables', []):
                (stage / 'plugins' / record['name'] / safe_path(relative)).chmod(0o755)
        validate_snapshot(stage)
        plugins = root / "plugins"
        require(not plugins.is_symlink(), "Refusing a symlink output directory")
        if plugins.exists():
            shutil.rmtree(plugins)
        shutil.move(str(stage / "plugins"), plugins)
        for name in (".agents/plugins/marketplace.json", "catalog.lock.json", "CATALOG.md"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((stage / name).read_bytes())


def validate_snapshot(root):
    marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "catalog.lock.json").read_text(encoding="utf-8"))
    require(NAME.fullmatch(marketplace.get("name", "")), "Invalid marketplace name")
    require(lock.get("format") == 1, "Unsupported lock format")
    entries = marketplace["plugins"]
    records = {r["name"]: r for r in lock["plugins"]}
    require(len(records) == len(lock["plugins"]) == len(entries) and bool(entries), "Lock/catalog mismatch")
    require(len({e["name"] for e in entries}) == len(entries), "Duplicate marketplace entry")
    expected_files = set()
    for entry in entries:
        name = entry["name"]
        require(NAME.fullmatch(name) and name in records, "Invalid plugin identity")
        record = records[name]
        require(REPO.fullmatch(record["repository"]) and SHA.fullmatch(record["commit"]), "Invalid source lock")
        require(record["version"] == record["tag"].removeprefix("v") and SEMVER.fullmatch(record["version"]), "Invalid release version")
        require(entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "Unexpected install policy")
        source = entry["source"]
        if record.get("bootstrap") is True:
            require(source == {"source": "git-subdir", "url": f"https://github.com/{record['repository']}.git", "path": "./" + safe_path(record["path"]), "sha": record["commit"]}, "Invalid bootstrap source")
            continue
        require(source == {"source": "local", "path": f"./plugins/{name}"}, "Invalid local source")
        folder = root / "plugins" / name
        require(not folder.is_symlink(), "Symlink plugin directory")
        files = {}
        for target in folder.rglob("*"):
            require(not target.is_symlink(), "Symlink in snapshot")
            if target.is_file():
                relative = target.relative_to(folder).as_posix()
                files[relative] = target.read_bytes()
                expected_files.add(target.relative_to(root).as_posix())
        manifest, interface = validate_plugin(files)
        require(set(record.get('executables', [])) <= files.keys(), 'Unknown executable in lock')
        if os.name != 'nt':
            actual_executables = {n for n in files if (folder / n).stat().st_mode & 0o111}
            require(actual_executables == set(record.get('executables', [])), 'Executable file mode mismatch')
        require(manifest["name"] == name and manifest["version"] == record["version"], "Snapshot identity mismatch")
        require(interface["category"] == entry["category"], "Category mismatch")
        require({n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())} == record["files"], "Snapshot checksum mismatch")
    plugin_root = root / "plugins"
    actual_files = set()
    if plugin_root.exists():
        require(not plugin_root.is_symlink(), "Symlink plugin root")
        for target in plugin_root.rglob("*"):
            require(not target.is_symlink(), "Symlink in plugins directory")
            if target.is_file():
                actual_files.add(target.relative_to(root).as_posix())
    require(actual_files == expected_files, "Untracked plugin files in snapshot")
    return len(entries)


def bundle(root):
    validate_snapshot(root)
    lock = json.loads((root / "catalog.lock.json").read_text(encoding="utf-8"))
    require(not any(r.get("bootstrap") for r in lock["plugins"]), "Run sync before creating a self-contained bundle")
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / "chatgpt-plugin-marketplace.zip"
    files = [root / ".agents/plugins/marketplace.json", root / "catalog.lock.json", root / "CATALOG.md", root / "README.md", root / "LICENSE"]
    files += sorted(p for p in (root / "plugins").rglob("*") if p.is_file())
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        executable_paths = {f"plugins/{r['name']}/{n}" for r in lock['plugins'] for n in r.get('executables', [])}
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if info.filename in executable_paths else 0o100644) << 16
            z.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "validate", "bundle"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "sync":
        config = json.loads((root / "marketplace.config.json").read_text(encoding="utf-8"))
        install(root, collect(config, GitHub(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))))
    elif args.command == "bundle":
        print(bundle(root))
    print(f"Verified {validate_snapshot(root)} plugin(s)")


if __name__ == "__main__":
    main()
