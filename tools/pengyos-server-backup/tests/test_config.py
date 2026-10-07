"""Tests for the backup planner schema + validator (pengyos_backup.config).

These exercise :func:`load_config` (file path) and :func:`validate`
(in-memory document) across the happy path and every schema violation.
"""

from __future__ import annotations

import textwrap

import pytest

from pengyos_backup.config import (
    Retention,
    Schedule,
    Target,
    validate,
)
from pengyos_backup.errors import ConfigError

# A representative, valid config used by the happy-path tests.
VALID_YAML = textwrap.dedent(
    """
    version: 1

    sources:
      - /etc
      - /home
      - /var/lib/pengyos

    targets:
      local:
        path: /var/backups/pengyos
      remote:
        url: s3:example.com/pengyos
        host: backup-host

    schedule:
      on_calendar: "Mon..Fri *-*-* 02:30:00"
      randomized_delay: "10min"

    retention:
      keep_last: 7
      keep_daily: 7
      keep_weekly: 4
      keep_monthly: 6
      keep_yearly: 0
      keep_within: "720h"
    """
)


def _valid_dict() -> dict:
    """The parsed form of :data:`VALID_YAML`."""

    import yaml

    return yaml.safe_load(VALID_YAML)


# --- happy path --------------------------------------------------------------


def test_load_config_parses_every_section(tmp_path):
    cfg = tmp_path / "backup.yaml"
    cfg.write_text(VALID_YAML, encoding="utf-8")

    from pengyos_backup.config import load_config

    plan = load_config(cfg)

    assert plan.version == 1
    assert plan.sources == ("/etc", "/home", "/var/lib/pengyos")
    assert len(plan.targets) == 2
    assert plan.schedule.on_calendar == "Mon..Fri *-*-* 02:30:00"
    assert plan.schedule.randomized_delay == "10min"
    assert plan.retention.keep_last == 7
    assert plan.retention.keep_within == "720h"


def test_local_and_remote_target_split():
    plan = validate(_valid_dict())
    assert len(plan.local_targets) == 1
    assert len(plan.remote_targets) == 1

    local = plan.local_targets[0]
    assert local.kind == "local"
    assert local.path == "/var/backups/pengyos"
    assert local.repo == "/var/backups/pengyos"

    remote = plan.remote_targets[0]
    assert remote.kind == "remote"
    assert remote.url == "s3:example.com/pengyos"
    assert remote.host == "backup-host"
    assert remote.repo == "s3:example.com/pengyos"


def test_only_local_target_is_valid(tmp_path):
    yaml_text = textwrap.dedent(
        """
        version: 1
        sources:
          - /etc
        targets:
          local:
            path: /var/backups
        schedule:
          on_calendar: daily
        """
    )
    cfg = tmp_path / "backup.yaml"
    cfg.write_text(yaml_text, encoding="utf-8")

    from pengyos_backup.config import load_config

    plan = load_config(cfg)
    assert len(plan.targets) == 1
    assert plan.local_targets[0].path == "/var/backups"
    assert plan.remote_targets == ()


def test_retention_is_optional():
    doc = {
        "version": 1,
        "sources": ["/etc"],
        "targets": {"local": {"path": "/var/backups"}},
        "schedule": {"on_calendar": "daily"},
    }
    plan = validate(doc)
    assert plan.retention == Retention()


def test_randomized_delay_is_optional():
    doc = {
        "version": 1,
        "sources": ["/etc"],
        "targets": {"local": {"path": "/var/backups"}},
        "schedule": {"on_calendar": "daily"},
    }
    plan = validate(doc)
    assert plan.schedule.randomized_delay is None


# --- structural errors -------------------------------------------------------


def test_empty_document_is_rejected():
    with pytest.raises(ConfigError, match="empty"):
        validate(None)


def test_top_level_not_a_mapping():
    with pytest.raises(ConfigError, match="mapping"):
        validate(["just", "a", "list"])


def test_missing_version():
    doc = _valid_dict()
    del doc["version"]
    with pytest.raises(ConfigError, match="version"):
        validate(doc)


def test_unsupported_version():
    doc = _valid_dict()
    doc["version"] = 99
    with pytest.raises(ConfigError, match="unsupported schema version"):
        validate(doc)


def test_missing_sources():
    doc = _valid_dict()
    del doc["sources"]
    with pytest.raises(ConfigError, match="sources"):
        validate(doc)


def test_empty_sources():
    doc = _valid_dict()
    doc["sources"] = []
    with pytest.raises(ConfigError, match="non-empty list"):
        validate(doc)


def test_sources_must_be_strings():
    doc = _valid_dict()
    doc["sources"] = ["/etc", 5]
    with pytest.raises(ConfigError, match="non-empty string"):
        validate(doc)


def test_missing_targets():
    doc = _valid_dict()
    del doc["targets"]
    with pytest.raises(ConfigError, match="targets"):
        validate(doc)


def test_no_local_or_remote_target():
    doc = _valid_dict()
    doc["targets"] = {"bogus": {"path": "/x"}}
    with pytest.raises(ConfigError, match="at least one"):
        validate(doc)


def test_local_target_missing_path():
    doc = _valid_dict()
    doc["targets"] = {"local": {"host": "h"}}
    with pytest.raises(ConfigError, match="path"):
        validate(doc)


def test_local_path_must_be_string():
    doc = _valid_dict()
    doc["targets"] = {"local": {"path": 123}}
    with pytest.raises(ConfigError, match="path"):
        validate(doc)


def test_remote_target_missing_url():
    doc = _valid_dict()
    doc["targets"] = {"remote": {"host": "h"}}
    with pytest.raises(ConfigError, match="url"):
        validate(doc)


def test_missing_schedule():
    doc = _valid_dict()
    del doc["schedule"]
    with pytest.raises(ConfigError, match="schedule"):
        validate(doc)


def test_schedule_missing_on_calendar():
    doc = _valid_dict()
    doc["schedule"] = {"randomized_delay": "5min"}
    with pytest.raises(ConfigError, match="on_calendar"):
        validate(doc)


# --- retention validation ----------------------------------------------------


def test_retention_negative_rejected():
    doc = _valid_dict()
    doc["retention"] = {"keep_last": -1}
    with pytest.raises(ConfigError, match="non-negative"):
        validate(doc)


def test_retention_non_int_rejected():
    doc = _valid_dict()
    doc["retention"] = {"keep_last": "7"}
    with pytest.raises(ConfigError, match="integer"):
        validate(doc)


def test_retention_bool_rejected():
    # bool is a subclass of int; it must not sneak through.
    doc = _valid_dict()
    doc["retention"] = {"keep_last": True}
    with pytest.raises(ConfigError, match="integer"):
        validate(doc)


def test_retention_keep_within_must_be_string():
    doc = _valid_dict()
    doc["retention"] = {"keep_within": 720}
    with pytest.raises(ConfigError, match="keep_within"):
        validate(doc)


# --- never-zero-kept-snapshots rule ------------------------------------------
#
# A retention policy that keeps zero snapshots is a footgun: restic/rustic
# `forget --prune` would delete every snapshot in the repo. The planner rejects
# such a policy at planning time so the restore proof has teeth. An *absent*
# policy (no retention section) is a valid keep-all no-op and is NOT rejected.


def test_retention_all_zero_rejected():
    # Every keep count present and zero -> keeps zero snapshots.
    doc = _valid_dict()
    doc["retention"] = {
        "keep_last": 0,
        "keep_daily": 0,
        "keep_weekly": 0,
        "keep_monthly": 0,
        "keep_yearly": 0,
    }
    with pytest.raises(ConfigError, match="zero snapshots"):
        validate(doc)


def test_retention_single_zero_rejected():
    # A lone keep_last: 0 keeps zero snapshots, even though the field is set.
    doc = _valid_dict()
    doc["retention"] = {"keep_last": 0}
    with pytest.raises(ConfigError, match="zero snapshots"):
        validate(doc)


def test_retention_zero_with_positive_keep_is_valid():
    # keep_daily: 0 alongside keep_last: 3 keeps three snapshots — valid.
    doc = _valid_dict()
    doc["retention"] = {"keep_last": 3, "keep_daily": 0}
    plan = validate(doc)
    assert plan.retention.keep_last == 3
    assert plan.retention.keep_daily == 0


def test_retention_only_keep_within_is_valid():
    # keep_within is a duration, not a count. `--keep-within 90d` keeps every
    # snapshot within the window, so it is NOT a zero-keep policy — it is valid
    # on its own (the rule only rejects policies whose keep *counts* are all 0).
    doc = _valid_dict()
    doc["retention"] = {"keep_within": "90d"}
    plan = validate(doc)
    assert plan.retention.keep_within == "90d"
    assert plan.retention.prune_flags() == ["--keep-within", "90d"]


def test_retention_absent_is_valid_keep_all():
    # No retention section at all -> keep-all forget --prune (valid no-op).
    doc = {
        "version": 1,
        "sources": ["/etc"],
        "targets": {"local": {"path": "/var/backups"}},
        "schedule": {"on_calendar": "daily"},
    }
    plan = validate(doc)
    assert plan.retention == Retention()


def test_retention_empty_mapping_is_valid_keep_all():
    # An explicit but empty retention mapping is the same as absent.
    doc = {
        "version": 1,
        "sources": ["/etc"],
        "targets": {"local": {"path": "/var/backups"}},
        "schedule": {"on_calendar": "daily"},
        "retention": {},
    }
    plan = validate(doc)
    assert plan.retention == Retention()


def test_zero_keep_message_names_the_field(tmp_path):
    # The error points at the offending field so the operator can fix it fast.
    cfg = tmp_path / "backup.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            version: 1
            sources:
              - /etc
            targets:
              local:
                path: /var/backups
            schedule:
              on_calendar: daily
            retention:
              keep_last: 0
            """
        ),
        encoding="utf-8",
    )
    from pengyos_backup.config import load_config

    with pytest.raises(ConfigError, match="keep_last"):
        load_config(cfg)


# --- missing file ------------------------------------------------------------


def test_missing_file_raises_config_error(tmp_path):
    from pengyos_backup.config import load_config

    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does-not-exist.yaml")


# --- minimal fallback reader (no PyYAML) -------------------------------------


def test_minimal_fallback_reader(monkeypatch):
    # Force the fallback path by making `import yaml` fail.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("simulated: PyYAML unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from pengyos_backup.config import load_config

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        cfg = f"{d}/backup.yaml"
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(VALID_YAML)
        plan = load_config(cfg)

    assert plan.version == 1
    assert plan.sources == ("/etc", "/home", "/var/lib/pengyos")
    assert plan.schedule.on_calendar == "Mon..Fri *-*-* 02:30:00"
    assert plan.retention.keep_last == 7
    assert plan.remote_targets[0].url == "s3:example.com/pengyos"


def test_minimal_reader_handles_comments_and_quotes():
    from pengyos_backup.config import _minimal_yaml_load

    text = (
        "# a comment\n"
        "version: 1\n"
        "sources:\n"
        "  - /etc\n"
        '  - "/home/x"\n'
        "targets:\n"
        "  local:\n"
        "    path: /var/backups  # trailing comment\n"
        "schedule:\n"
        "  on_calendar: daily\n"
    )
    doc = _minimal_yaml_load(text)
    assert doc["version"] == 1
    assert doc["sources"] == ["/etc", "/home/x"]
    assert doc["targets"]["local"]["path"] == "/var/backups"
    assert doc["schedule"]["on_calendar"] == "daily"


def test_non_mapping_top_level_is_rejected():
    # A top-level scalar (or list) is not a valid config.
    with pytest.raises(ConfigError, match="mapping"):
        validate("just a scalar")


# --- serialization (to_dict) -------------------------------------------------


def _dumps(obj) -> str:
    import json

    return json.dumps(obj)


def test_backup_plan_to_dict_matches_sections():
    plan = validate(_valid_dict())
    d = plan.to_dict()
    # The top-level keys mirror the backup.yaml sections, in order.
    assert list(d.keys()) == ["version", "sources", "targets", "schedule", "retention"]
    assert d["version"] == 1
    assert d["sources"] == ["/etc", "/home", "/var/lib/pengyos"]


def test_to_dict_targets_are_kind_specific():
    plan = validate(_valid_dict())
    d = plan.to_dict()
    local, remote = d["targets"]
    # local carries path, not url; remote carries url, not path.
    assert local == {"kind": "local", "path": "/var/backups/pengyos"}
    assert remote == {
        "kind": "remote",
        "url": "s3:example.com/pengyos",
        "host": "backup-host",
    }


def test_to_dict_drops_unset_optional_fields():
    # A minimal config with no randomized_delay / retention renders without
    # nulls (the whole point of the *_dict serializers + _clean).
    doc = {
        "version": 1,
        "sources": ["/etc"],
        "targets": {"local": {"path": "/var/backups"}},
        "schedule": {"on_calendar": "daily"},
    }
    plan = validate(doc)
    d = plan.to_dict()
    assert d["schedule"] == {"on_calendar": "daily"}  # no randomized_delay key
    assert d["retention"] == {}  # nothing kept -> empty object, no nulls
    assert "null" not in _dumps(d)  # sanity: no JSON nulls anywhere


def test_to_dict_preserves_zero_retention_value():
    # keep_yearly: 0 is a real value (not None) and must survive serialization.
    # It is paired with a positive keep so the policy is valid (a lone zero is
    # rejected as "keeps zero snapshots" — see test_retention_all_zero_rejected).
    doc = {
        "version": 1,
        "sources": ["/etc"],
        "targets": {"local": {"path": "/var/backups"}},
        "schedule": {"on_calendar": "daily"},
        "retention": {"keep_last": 3, "keep_yearly": 0},
    }
    plan = validate(doc)
    assert plan.to_dict()["retention"] == {"keep_last": 3, "keep_yearly": 0}


def test_to_dict_is_json_roundtrip():
    import json

    plan = validate(_valid_dict())
    # to_dict must produce only JSON-native types.
    text = json.dumps(plan.to_dict())
    assert json.loads(text) == plan.to_dict()


# --- retention.prune_flags() (encode the policy as forget argv) ---------------
#
# `prune_flags` is the retention policy encoded as the tokens restic/rustic
# `forget` consumes — the single source of truth the plan's forget argv consumes.


def test_prune_flags_maps_every_set_keep():
    plan = validate(_valid_dict())
    assert plan.retention.prune_flags() == [
        "--keep-last",
        "7",
        "--keep-daily",
        "7",
        "--keep-weekly",
        "4",
        "--keep-monthly",
        "6",
        "--keep-yearly",
        "0",
        "--keep-within",
        "720h",
    ]


def test_prune_flags_emits_zero_keep_value():
    # A zero keep is a real value (not "unset") and must be emitted.
    retention = Retention(keep_last=0)
    assert retention.prune_flags() == ["--keep-last", "0"]


def test_prune_flags_empty_policy_returns_empty_list():
    assert Retention().prune_flags() == []


def test_prune_flags_order_is_flag_then_value():
    # The encoding is strictly alternating flag/value, in canonical order.
    retention = Retention(keep_daily=2, keep_last=5)
    assert retention.prune_flags() == ["--keep-last", "5", "--keep-daily", "2"]


def test_prune_flags_matches_forget_argv_source():
    # The plan's _forget_argv must consume prune_flags verbatim (single source
    # of truth): the keep flags between `--repo <repo>` and `--prune` are exactly
    # prune_flags().
    from pengyos_backup.units import _forget_argv

    plan = validate(_valid_dict())
    target = plan.local_targets[0]
    argv = _forget_argv("restic", target, plan.retention)
    # restic forget --repo <repo> <prune_flags...> --prune
    assert argv[0:4] == ("restic", "forget", "--repo", target.repo)
    assert argv[-1] == "--prune"
    assert argv[4:-1] == tuple(plan.retention.prune_flags())
