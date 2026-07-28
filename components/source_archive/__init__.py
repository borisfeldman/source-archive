from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path
import re
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
VERSION = "1.2.0"

CONF_DOWNLOAD_BUTTON = "download_button"
CONF_DOWNLOAD_PATH = "download_path"
CONF_FILENAME = "filename"
CONF_FILES = "files"
CONF_INCLUDE_CURRENT_CONFIG = "include_current_config"
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


def _validate_download_path(value: str) -> str:
    value = cv.string_strict(value)
    if not value.startswith("/") or "?" in value or "#" in value:
        raise cv.Invalid("download_path must be an absolute URL path")
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
            cv.Optional(CONF_FILES, default=list): cv.ensure_list(
                _validate_source_path
            ),
            cv.Optional(
                CONF_DOWNLOAD_PATH, default="/source.zip"
            ): _validate_download_path,
            cv.Optional(CONF_FILENAME, default="esphome-source.zip"): _validate_filename,
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


def _zip_info(filename: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _build_archive(config) -> tuple[bytes, int]:
    source_files: list[tuple[str, Path]] = []
    seen_paths: set[str] = set()

    if config[CONF_INCLUDE_CURRENT_CONFIG]:
        config_path = Path(CORE.config_path)
        source_files.append((config_path.name, config_path))
        seen_paths.add(config_path.name)

    for configured_path in config[CONF_FILES]:
        archive_path = Path(configured_path).as_posix()
        if archive_path in seen_paths:
            continue
        source_files.append((archive_path, CORE.relative_config_path(configured_path)))
        seen_paths.add(archive_path)

    manifest_files = []
    contents = []
    for archive_path, source_path in source_files:
        content = source_path.read_bytes()
        contents.append((archive_path, content))
        manifest_files.append(
            {
                "path": archive_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )

    manifest = json.dumps(
        {
            "component_version": VERSION,
            "configuration": Path(CORE.config_path).name,
            "esphome_version": ESPHOME_VERSION,
            "files": manifest_files,
            "format": 1,
        },
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

    return output.getvalue(), len(contents)


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
        "source_archive %s embedded %s files in %s (%d bytes)",
        VERSION,
        file_count,
        config[CONF_FILENAME],
        len(archive),
    )