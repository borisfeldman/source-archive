from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit, urlunsplit
import zipfile

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_JS_INCLUDE, __version__ as ESPHOME_VERSION
from esphome.core import CORE, EsphomeError
import esphome.final_validate as fv

CODEOWNERS = ["@borisfeldman"]
DEPENDENCIES = ["web_server"]
AUTO_LOAD = ["web_server_base"]

# Bump together with the git tag that releases it.
VERSION = "1.4.0"

CONF_DOWNLOAD_BUTTON = "download_button"
CONF_DOWNLOAD_PATH = "download_path"
CONF_FILENAME = "filename"
CONF_FILES = "files"
CONF_INCLUDE_CURRENT_CONFIG = "include_current_config"
CONF_INCLUDE_GIT_METADATA = "include_git_metadata"
CONF_MANIFEST_ONLY = "manifest_only"
CONF_MAX_SIZE = "max_size"
CONF_RAW_DATA_ID = "raw_data_id"

WEB_SERVER_DOMAIN = "web_server"
BUTTON_SCRIPT = "source_archive_link.js"

source_archive_ns = cg.esphome_ns.namespace("source_archive")
SourceArchive = source_archive_ns.class_("SourceArchive", cg.Component)

LOGGER = logging.getLogger(__name__)

# Interpolated unquoted into the Content-Disposition header, so keep it boring.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.zip$")

_DOWNLOAD_PATH_RE = re.compile(r'^const DOWNLOAD_PATH = "[^"]*";$', re.MULTILINE)

# web_server registers its handler first, so it would silently win these routes.
_RESERVED_DOWNLOAD_PATHS = frozenset({"/", "/0.css", "/0.js", "/events"})


def _validate_download_path(value: str) -> str:
    value = cv.string_strict(value)
    if not value.startswith("/") or "?" in value or "#" in value:
        raise cv.Invalid("download_path must be an absolute URL path")
    if value in _RESERVED_DOWNLOAD_PATHS:
        raise cv.Invalid(f"'{value}' is already served by web_server, pick another path")
    return value


def _validate_filename(value: str) -> str:
    value = cv.string_strict(value)
    if not _FILENAME_RE.match(value):
        raise cv.Invalid(
            "filename must end in .zip and use only letters, digits, dots, "
            "dashes and underscores"
        )
    return value


def _validate_source_path(value: str) -> str:
    value = cv.string_strict(value)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise cv.Invalid("source archive files must stay inside the config directory")
    if path.parts and path.parts[0] == ".esphome":
        raise cv.Invalid(
            "refusing to embed '.esphome': it holds ESPHome's working state, not your "
            "configuration"
        )
    if path.name == "secrets.yaml":
        raise cv.Invalid(
            "refusing to embed secrets.yaml: the archive is served over HTTP and "
            "would expose every secret it contains"
        )
    cv.file_(value)
    return value


def _validate_has_sources(config):
    if not config[CONF_INCLUDE_CURRENT_CONFIG] and not config[CONF_FILES]:
        raise cv.Invalid(
            "the archive would be empty: list files under 'files' or leave "
            "'include_current_config' enabled"
        )
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(SourceArchive),
            cv.GenerateID(CONF_RAW_DATA_ID): cv.declare_id(cg.uint8),
            cv.Optional(CONF_INCLUDE_CURRENT_CONFIG, default=True): cv.boolean,
            cv.Optional(CONF_INCLUDE_GIT_METADATA, default=True): cv.boolean,
            cv.Optional(CONF_FILES, default=list): cv.ensure_list(
                _validate_source_path
            ),
            cv.Optional(
                CONF_DOWNLOAD_PATH, default="/source.zip"
            ): _validate_download_path,
            cv.Optional(CONF_FILENAME, default="esphome-source.zip"): _validate_filename,
            cv.Optional(CONF_MANIFEST_ONLY, default=False): cv.boolean,
            cv.Optional(CONF_MAX_SIZE, default="64kB"): cv.validate_bytes,
            cv.Optional(CONF_DOWNLOAD_BUTTON, default=False): cv.boolean,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.require_esphome_version(2026, 2, 0),
    _validate_has_sources,
)


def _generate_button_script(download_path: str) -> Path:
    bundled = Path(__file__).parent / BUTTON_SCRIPT
    script, replaced = _DOWNLOAD_PATH_RE.subn(
        f"const DOWNLOAD_PATH = {json.dumps(download_path)};",
        bundled.read_text(encoding="utf-8"),
    )
    if replaced != 1:
        raise cv.Invalid(f"could not set DOWNLOAD_PATH in {BUTTON_SCRIPT}")

    # web_server reads js_include off the filesystem, so the script has to land somewhere.
    generated = CORE.relative_internal_path("source_archive", BUTTON_SCRIPT)
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text(script, encoding="utf-8")
    return generated


def _final_validate(config):
    if not config[CONF_DOWNLOAD_BUTTON]:
        return config

    web_server_config = fv.full_config.get()[WEB_SERVER_DOMAIN]
    if CONF_JS_INCLUDE in web_server_config:
        raise cv.Invalid(
            f"'{CONF_DOWNLOAD_BUTTON}' points web_server's '{CONF_JS_INCLUDE}' at a "
            f"generated script, but '{CONF_JS_INCLUDE}' is already set and web_server "
            f"accepts only one. Either drop '{CONF_DOWNLOAD_BUTTON}' and add the button "
            f"to your own script, or remove '{CONF_JS_INCLUDE}'."
        )

    web_server_config[CONF_JS_INCLUDE] = str(
        _generate_button_script(config[CONF_DOWNLOAD_PATH])
    )
    return config


FINAL_VALIDATE_SCHEMA = _final_validate


# Git metadata is provenance, not a build input: every helper below returns
# nothing rather than raising, so a missing git, a missing repository or a
# hostile checkout can never fail a compile.
_GIT_TIMEOUT = 5

_repository_root_cache: dict[Path, Path | None] = {}


def _run_git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (
        OSError,
        subprocess.SubprocessError,
    ):
        return None

    # Only line endings are trimmed: the first porcelain column is a space when
    # a file is modified in the worktree but not staged.
    return result.stdout.strip("\r\n") or None


def _git_succeeds(repo_root: Path, *args: str) -> bool:
    """Run git for its exit code alone, where a non-zero answer is not an error."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0


def _sanitize_git_remote(remote: str) -> str:
    if "://" not in remote:
        return remote

    # A remote too malformed to parse is dropped rather than passed through:
    # it may still be carrying credentials.
    try:
        parsed = urlsplit(remote)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return ""

    if port is not None:
        hostname = f"{hostname}:{port}"

    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def _git_repository_root(path: Path) -> Path | None:
    directory = path if path.is_dir() else path.parent
    try:
        directory = directory.resolve()
    except OSError:
        return None

    if directory in _repository_root_cache:
        return _repository_root_cache[directory]

    root = _run_git(directory, "rev-parse", "--show-toplevel")
    resolved = Path(root).resolve() if root is not None else None
    _repository_root_cache[directory] = resolved
    return resolved


def _repository_relative_path(file_path: Path, repo_root: Path) -> str | None:
    try:
        return file_path.resolve().relative_to(repo_root).as_posix()
    except (OSError, ValueError):
        return None


def _git_file_is_tracked(repo_root: Path, repository_path: str) -> bool:
    return _git_succeeds(
        repo_root, "ls-files", "--error-unmatch", "--", repository_path
    )


def _git_file_is_ignored(repo_root: Path, repository_path: str) -> bool:
    return _git_succeeds(repo_root, "check-ignore", "-q", "--", repository_path)


def _normalize_git_status(
    porcelain: str | None,
    *,
    tracked: bool,
    ignored: bool,
) -> str:
    if ignored:
        return "ignored"
    if not tracked:
        return "untracked"
    if not porcelain:
        return "clean"

    code = porcelain[:2].ljust(2)
    index_state = code[0] != " "
    worktree_state = code[1] != " "

    if index_state and worktree_state:
        return "modified_and_staged"
    if index_state:
        return "staged"
    if worktree_state:
        return "modified"
    return "clean"


def _git_file_metadata(source_path: Path, config_repo_root: Path) -> dict:
    repo_root = _git_repository_root(source_path)
    repository_path = (
        _repository_relative_path(source_path, repo_root)
        if repo_root is not None
        else None
    )

    if repo_root != config_repo_root or repository_path is None:
        return {"status": "outside_repository", "tracked": False}

    tracked = _git_file_is_tracked(repo_root, repository_path)
    ignored = _git_file_is_ignored(repo_root, repository_path)
    status_output = _run_git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        repository_path,
    )

    metadata = {
        "repository": "config",
        "repository_path": repository_path,
        "status": _normalize_git_status(
            status_output, tracked=tracked, ignored=ignored
        ),
        "tracked": tracked,
    }

    if tracked:
        # Absent for a file that is staged but never committed.
        blob = _run_git(repo_root, "rev-parse", f"HEAD:{repository_path}")
        if blob is not None:
            metadata["blob"] = blob

    return metadata


def _git_repository_metadata(config_path: Path) -> tuple[Path | None, dict | None]:
    repo_root = _git_repository_root(config_path)
    if repo_root is None:
        return None, None

    repository_path = _repository_relative_path(config_path, repo_root)
    if repository_path is None:
        return None, None

    remote = _run_git(repo_root, "remote", "get-url", "origin")
    metadata = {
        # Empty on a detached HEAD, and dropped below along with the other
        # fields a repository without commits or without a remote cannot answer.
        "branch": _run_git(repo_root, "branch", "--show-current"),
        "commit": _run_git(repo_root, "rev-parse", "HEAD"),
        "commit_timestamp": _run_git(repo_root, "show", "-s", "--format=%cI", "HEAD"),
        "configuration_path": repository_path,
        "configuration_tracked": _git_file_is_tracked(repo_root, repository_path),
        "describe": _run_git(repo_root, "describe", "--tags", "--always", "--dirty"),
        "dirty": bool(
            _run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
        ),
        "remote": _sanitize_git_remote(remote) if remote is not None else None,
    }

    return repo_root, {
        key: value
        for key, value in metadata.items()
        if value is not None and value != ""
    }


def _zip_info(filename: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _build_archive(config) -> tuple[bytes, int]:
    source_files: list[tuple[str, Path]] = []
    seen_paths: set[str] = set()
    _repository_root_cache.clear()

    config_path = Path(CORE.config_path)

    if config[CONF_INCLUDE_CURRENT_CONFIG]:
        source_files.append((config_path.name, config_path))
        seen_paths.add(config_path.name)

    for configured_path in config[CONF_FILES]:
        archive_path = Path(configured_path).as_posix()
        if archive_path in seen_paths:
            continue
        source_files.append((archive_path, CORE.relative_config_path(configured_path)))
        seen_paths.add(archive_path)

    config_repo_root, repository_git = (
        _git_repository_metadata(config_path)
        if config[CONF_INCLUDE_GIT_METADATA]
        else (None, None)
    )
    manifest_only = config[CONF_MANIFEST_ONLY]

    manifest_files = []
    contents = []
    tracked_by_git = 0
    for archive_path, source_path in source_files:
        content = source_path.read_bytes()
        if not manifest_only:
            contents.append((archive_path, content))
        file_manifest = {
            "path": archive_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

        if config_repo_root is not None:
            file_git = _git_file_metadata(source_path, config_repo_root)
            file_manifest["git"] = file_git
            if file_git["status"] != "outside_repository":
                tracked_by_git += 1

        manifest_files.append(file_manifest)

    manifest_data = {
        "component_version": VERSION,
        "configuration": config_path.name,
        "esphome_version": ESPHOME_VERSION,
        "files": manifest_files,
        "format": 2,
    }
    if manifest_only:
        # So a reader knows the entries are missing by choice, not truncated.
        manifest_data["manifest_only"] = True
    if repository_git is not None:
        manifest_data["git"] = repository_git
        LOGGER.debug(
            "source_archive collected Git metadata for %d of %d files",
            tracked_by_git,
            len(source_files),
        )

    manifest = json.dumps(
        manifest_data,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for archive_path, content in contents:
            archive.writestr(_zip_info(archive_path), content, compresslevel=9)
        archive.writestr(_zip_info("SOURCE-MANIFEST.json"), manifest, compresslevel=9)

    return output.getvalue(), len(manifest_files)


async def to_code(config):
    archive, file_count = _build_archive(config)

    max_size = config[CONF_MAX_SIZE]
    if len(archive) > max_size:
        raise EsphomeError(
            f"Source archive is {len(archive)} bytes, over the {max_size} byte "
            f"'{CONF_MAX_SIZE}' limit. Drop entries from '{CONF_FILES}', or raise "
            f"'{CONF_MAX_SIZE}' if the firmware has room for it."
        )

    archive_data = cg.progmem_array(config[CONF_RAW_DATA_ID], list(archive))

    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_archive(archive_data, len(archive)))
    cg.add(var.set_download_path(config[CONF_DOWNLOAD_PATH]))
    cg.add(var.set_filename(config[CONF_FILENAME]))

    LOGGER.info(
        "source_archive %s %s %s files in %s (%d bytes)",
        VERSION,
        "listed" if config[CONF_MANIFEST_ONLY] else "embedded",
        file_count,
        config[CONF_FILENAME],
        len(archive),
    )