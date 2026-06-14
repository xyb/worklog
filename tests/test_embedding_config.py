"""Tests for embedding config resolution: defaults < config.ini < env < CLI flag.

The whole point of this layering is that a user can set a stable backend in
~/.config/worklog/config.ini, override per-shell with $WORKLOG_EMBED_*, and
override per-invocation with --endpoint/--model/... on `wl query`/`wl reindex`.
"""
import types
import pytest

from worklog import config as cfg
from worklog.xdg import _resolve_config_path


def _args(**kw):
    """A fake argparse.Namespace carrying only the embedding override flags."""
    base = {"endpoint": None, "model": None, "dimensions": None, "api_key": None, "query_prompt": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def cfg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # ensure no embedding env leaks in from the host
    for v in ("WORKLOG_EMBED_ENDPOINT", "WORKLOG_EMBED_MODEL", "WORKLOG_EMBED_DIMENSIONS", "WORKLOG_EMBED_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def _write_ini(body):
    p = _resolve_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


class TestDefaults:
    def test_defaults_when_nothing_set(self, cfg_home):
        c = cfg.resolve_embedding_config(_args())
        assert c["endpoint"] == cfg.EMBED_DEFAULTS["endpoint"]
        assert c["model"] == cfg.EMBED_DEFAULTS["model"]
        # dimensions default is "native" (None / empty) — not forced
        assert c["dimensions"] is None
        assert c["source"]["endpoint"] == "default"

    def test_config_path_under_xdg(self, cfg_home):
        assert str(_resolve_config_path()).endswith("worklog/config.ini")
        assert str(cfg_home / "cfg") in str(_resolve_config_path())


class TestIniOverridesDefault:
    def test_ini_endpoint_and_model(self, cfg_home):
        _write_ini("[embedding]\nendpoint = http://host:9/v1/embeddings\nmodel = my-model\n")
        c = cfg.resolve_embedding_config(_args())
        assert c["endpoint"] == "http://host:9/v1/embeddings"
        assert c["model"] == "my-model"
        assert c["source"]["endpoint"] == "config"

    def test_ini_dimensions_parsed_int(self, cfg_home):
        _write_ini("[embedding]\ndimensions = 512\n")
        c = cfg.resolve_embedding_config(_args())
        assert c["dimensions"] == 512


class TestEnvOverridesIni:
    def test_env_beats_ini(self, cfg_home, monkeypatch):
        _write_ini("[embedding]\nendpoint = http://from-ini/v1\n")
        monkeypatch.setenv("WORKLOG_EMBED_ENDPOINT", "http://from-env/v1")
        c = cfg.resolve_embedding_config(_args())
        assert c["endpoint"] == "http://from-env/v1"
        assert c["source"]["endpoint"] == "env"

    def test_env_model_and_dims(self, cfg_home, monkeypatch):
        monkeypatch.setenv("WORKLOG_EMBED_MODEL", "env-model")
        monkeypatch.setenv("WORKLOG_EMBED_DIMENSIONS", "256")
        c = cfg.resolve_embedding_config(_args())
        assert c["model"] == "env-model"
        assert c["dimensions"] == 256


class TestFlagOverridesEnv:
    def test_flag_beats_env_and_ini(self, cfg_home, monkeypatch):
        _write_ini("[embedding]\nendpoint = http://ini/v1\nmodel = ini-model\n")
        monkeypatch.setenv("WORKLOG_EMBED_ENDPOINT", "http://env/v1")
        c = cfg.resolve_embedding_config(_args(endpoint="http://flag/v1"))
        assert c["endpoint"] == "http://flag/v1"
        assert c["source"]["endpoint"] == "flag"
        # model still from ini (no flag for it)
        assert c["model"] == "ini-model"
        assert c["source"]["model"] == "config"

    def test_flag_dimensions(self, cfg_home, monkeypatch):
        monkeypatch.setenv("WORKLOG_EMBED_DIMENSIONS", "256")
        c = cfg.resolve_embedding_config(_args(dimensions=128))
        assert c["dimensions"] == 128
        assert c["source"]["dimensions"] == "flag"


class TestQueryPrompt:
    def test_default_is_qwen_template_with_placeholder(self, cfg_home):
        c = cfg.resolve_embedding_config(_args())
        assert "{query}" in c["query_prompt"]
        assert "retrieve relevant passages" in c["query_prompt"]
        assert c["source"]["query_prompt"] == "default"

    def test_empty_in_config_means_off_not_default(self, cfg_home):
        # an explicit empty value = "server already adds it, don't double" — must NOT
        # fall through to the default template
        _write_ini("[embedding]\nquery_prompt =\n")
        c = cfg.resolve_embedding_config(_args())
        assert c["query_prompt"] == ""
        assert c["source"]["query_prompt"] == "config"

    def test_custom_in_config(self, cfg_home):
        _write_ini("[embedding]\nquery_prompt = search: {query}\n")
        c = cfg.resolve_embedding_config(_args())
        assert c["query_prompt"] == "search: {query}"

    def test_empty_in_env_means_off(self, cfg_home, monkeypatch):
        monkeypatch.setenv("WORKLOG_EMBED_QUERY_PROMPT", "")
        c = cfg.resolve_embedding_config(_args())
        assert c["query_prompt"] == "" and c["source"]["query_prompt"] == "env"

    def test_flag_overrides(self, cfg_home):
        _write_ini("[embedding]\nquery_prompt = from ini {query}\n")
        c = cfg.resolve_embedding_config(_args(query_prompt="from flag {query}"))
        assert c["query_prompt"] == "from flag {query}" and c["source"]["query_prompt"] == "flag"

    def test_flag_empty_disables(self, cfg_home):
        _write_ini("[embedding]\nquery_prompt = from ini {query}\n")
        c = cfg.resolve_embedding_config(_args(query_prompt=""))
        assert c["query_prompt"] == "" and c["source"]["query_prompt"] == "flag"


class TestApiKey:
    def test_flag_sets_key(self, cfg_home):
        c = cfg.resolve_embedding_config(_args(api_key="sk-123"))
        assert c["api_key"] == "sk-123" and c["source"]["api_key"] == "flag"

    def test_env_sets_key(self, cfg_home, monkeypatch):
        monkeypatch.setenv("WORKLOG_EMBED_API_KEY", "sk-env")
        c = cfg.resolve_embedding_config(_args())
        assert c["api_key"] == "sk-env" and c["source"]["api_key"] == "env"


class TestCoerce:
    def test_empty_string_coerces_to_none(self):
        # a set-but-empty ini/env value for a normal field = "unset" → falls through
        assert cfg._coerce("endpoint", "   ") is None
        assert cfg._coerce("model", "") is None

    def test_none_passes_through(self):
        assert cfg._coerce("endpoint", None) is None


class TestSynonymMap:
    def test_empty_without_file(self, cfg_home):
        assert cfg.synonym_map() == {}

    def test_empty_when_file_has_no_synonyms_section(self, cfg_home):
        _write_ini("[embedding]\nmodel = m\n")          # file exists, but no [synonyms]
        assert cfg.synonym_map() == {}

    def test_group_expansion(self, cfg_home):
        _write_ini("[synonyms]\nNew York = NYC, NY\n")
        m = cfg.synonym_map()
        # configparser lowercases the option key, so the canonical comes back as "new york";
        # alias values keep their case.
        assert m["nyc"] == {"new york", "NYC", "NY"}
        assert m["new york"] == m["ny"] == m["nyc"]
