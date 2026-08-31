"""Configuration: config.ini via configparser, with environment overrides.

Any value may be overridden by an environment variable named
``CDL_<SECTION>_<KEY>``, e.g. ``CDL_STORE_DB_PATH`` or ``CDL_FFR_WEIGHT_COLUMN``.
Defaults are the Cursor development settings: every source is mock, so nothing ever
contacts a corporate endpoint unless the operator says so in config.ini.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import constants

ENV_PREFIX = "CDL"
ENV_CONFIG_PATH = "CDL_CONFIG"
CONFIG_FILE_NAME = "config.ini"
EXAMPLE_FILE_NAME = "config.example.ini"


class ConfigError(ValueError):
    """A configured value is not usable."""


def project_root() -> Path:
    """Repository root - src/cdl/config.py -> <root>."""
    return Path(__file__).resolve().parents[2]


DEFAULTS: dict[str, dict[str, str]] = {
    "treats": {
        "url": "",
        "library": "",
        "ttcpipp": constants.SOURCE_MOCK,
        "cksblmp": constants.SOURCE_MOCK,
        "ckovlmp": constants.SOURCE_MOCK,
        # The endpoint caps a result set. A read that comes back with exactly this many
        # rows is treated as truncated rather than complete (see treats/api.py).
        "max_rows": "20000",
        # A counterparty `doctor` may query to prove a table answers, instead of
        # reading the whole table. Local value: it never belongs in the repository.
        "probe_counterparty": "",
    },
    "ffr": {
        "source": constants.SOURCE_MOCK,
        "table": constants.TABLE_FFR_DEFAULT,
        "weight_column": "2025Q2",
        "excel_path": "",
    },
    "store": {
        "db_path": "./data/cross_desk_limit.db",
        "hold_ttl_minutes": "60",
        "busy_timeout_ms": "15000",
        # Optional: auto | true | false. "auto" detects a UNC path (see store/db.py).
        "network_path": "auto",
    },
    "paths": {
        "dev_cache": "./dev_cache",
        "mock_treats": "./data/mock_treats",
    },
}


@dataclass(frozen=True)
class TreatsSettings:
    """Where the three counterparty/limit tables come from."""

    url: str
    library: str
    ttcpipp: str
    cksblmp: str
    ckovlmp: str
    max_rows: int = 0
    probe_counterparty: str = ""

    def source_for(self, table: str) -> str:
        key = constants.TABLE_CONFIG_KEY.get(table)
        if key is None:
            raise ConfigError(f"table {table!r} has no configured source")
        return str(getattr(self, key))


@dataclass(frozen=True)
class FfrSettings:
    """FFR weighting table (CKBLOTP); see §7 of docs/PLAN.md."""

    source: str
    table: str
    weight_column: str
    excel_path: str


@dataclass(frozen=True)
class StoreSettings:
    """Shared holds/history database."""

    db_path: Path
    hold_ttl_minutes: int
    busy_timeout_ms: int
    network_path: str


@dataclass(frozen=True)
class PathsSettings:
    dev_cache: Path
    mock_treats: Path


@dataclass(frozen=True)
class Settings:
    treats: TreatsSettings
    ffr: FfrSettings
    store: StoreSettings
    paths: PathsSettings
    config_path: Path | None = None
    overrides: tuple[str, ...] = field(default_factory=tuple)
    #: Values that arrived wrapped in quotes; the quotes were stripped, and `doctor`
    #: reports them so the ini file gets cleaned up.
    quoted_values: tuple[str, ...] = field(default_factory=tuple)

    def source_summary(self) -> dict[str, str]:
        """Effective source per table, for the startup log and `doctor`."""
        summary = {
            table: self.treats.source_for(table) for table in constants.CONFIGURED_TABLES
        }
        summary[self.ffr.table] = self.ffr.source
        return summary


def find_config_path(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Explicit path, then $CDL_CONFIG, then ./config.ini, then <root>/config.ini."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    from_env = os.environ.get(ENV_CONFIG_PATH)
    if from_env:
        path = Path(from_env).expanduser()
        if not path.is_file():
            raise ConfigError(f"{ENV_CONFIG_PATH} points at a missing file: {path}")
        return path
    for candidate in (Path.cwd() / CONFIG_FILE_NAME, project_root() / CONFIG_FILE_NAME):
        if candidate.is_file():
            return candidate
    return None


def _env_key(section: str, key: str) -> str:
    return f"{ENV_PREFIX}_{section.upper()}_{key.upper()}"


def unquote(raw: str) -> str:
    """Strip one matching pair of surrounding quotes, then whitespace.

    An ini file is not Python: `url = "http://..."` would otherwise keep its quotes and
    the endpoint call would fail with a value that looks correct in the file. Quotes
    around a value are always a mistake here, so they are removed rather than reported.
    """
    text = str(raw if raw is not None else "").strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def looks_quoted(raw: str) -> bool:
    """True when a raw config value still carries a quote character."""
    return any(quote in str(raw or "") for quote in ("'", '"'))


def _resolve_path(raw: str) -> Path:
    path = Path(unquote(raw)).expanduser()
    if path.is_absolute() or str(path).startswith("\\\\"):
        return path
    return (project_root() / path).resolve()


def _positive_int(section: str, key: str, raw: str) -> int:
    try:
        value = int(unquote(raw))
    except (TypeError, ValueError) as error:
        raise ConfigError(f"[{section}] {key} must be a whole number, got {raw!r}") from error
    if value <= 0:
        raise ConfigError(f"[{section}] {key} must be greater than zero, got {value}")
    return value


def _choice(section: str, key: str, raw: str, allowed: tuple[str, ...]) -> str:
    value = unquote(raw).lower()
    if value not in allowed:
        raise ConfigError(
            f"[{section}] {key} must be one of {', '.join(allowed)}; got {raw!r}"
        )
    return value


def load_settings(config_path: str | os.PathLike[str] | None = None) -> Settings:
    """Read config.ini (if any), apply environment overrides, validate."""
    path = find_config_path(config_path)
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.read_dict(DEFAULTS)
    if path is not None:
        parser.read(path, encoding="utf-8")

    overrides: list[str] = []
    for section, keys in DEFAULTS.items():
        for key in keys:
            env_name = _env_key(section, key)
            if env_name in os.environ:
                parser.set(section, key, os.environ[env_name])
                overrides.append(env_name)

    quoted = tuple(
        f"[{section}] {key}"
        for section, keys in DEFAULTS.items()
        for key in keys
        if looks_quoted(parser.get(section, key))
    )

    treats = TreatsSettings(
        url=unquote(parser.get("treats", "url")),
        library=unquote(parser.get("treats", "library")),
        ttcpipp=_choice("treats", "ttcpipp", parser.get("treats", "ttcpipp"),
                        constants.TABLE_SOURCES),
        cksblmp=_choice("treats", "cksblmp", parser.get("treats", "cksblmp"),
                        constants.TABLE_SOURCES),
        ckovlmp=_choice("treats", "ckovlmp", parser.get("treats", "ckovlmp"),
                        constants.TABLE_SOURCES),
        max_rows=_positive_int("treats", "max_rows", parser.get("treats", "max_rows")),
        probe_counterparty=unquote(parser.get("treats", "probe_counterparty")).upper(),
    )
    ffr = FfrSettings(
        source=_choice("ffr", "source", parser.get("ffr", "source"), constants.FFR_SOURCES),
        table=unquote(parser.get("ffr", "table")) or constants.TABLE_FFR_DEFAULT,
        weight_column=unquote(parser.get("ffr", "weight_column")),
        excel_path=unquote(parser.get("ffr", "excel_path")),
    )
    if not ffr.weight_column:
        raise ConfigError("[ffr] weight_column must name a quarter column, e.g. 2025Q2")
    store = StoreSettings(
        db_path=_resolve_path(parser.get("store", "db_path")),
        hold_ttl_minutes=_positive_int("store", "hold_ttl_minutes",
                                       parser.get("store", "hold_ttl_minutes")),
        busy_timeout_ms=_positive_int("store", "busy_timeout_ms",
                                      parser.get("store", "busy_timeout_ms")),
        network_path=_choice("store", "network_path", parser.get("store", "network_path"),
                            ("auto", "true", "false")),
    )
    paths = PathsSettings(
        dev_cache=_resolve_path(parser.get("paths", "dev_cache")),
        mock_treats=_resolve_path(parser.get("paths", "mock_treats")),
    )
    return Settings(
        treats=treats,
        ffr=ffr,
        store=store,
        paths=paths,
        config_path=path,
        overrides=tuple(overrides),
        quoted_values=quoted,
    )
