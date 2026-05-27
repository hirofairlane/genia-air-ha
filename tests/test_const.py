"""Minimal sanity checks for the LEGACY_UID_MAP and entities catalog.

These tests do NOT exercise HA itself — they only verify that our catalog is
internally consistent. Run with `pytest tests/`.
"""
# Import modules directly without going through the package __init__
# (which imports homeassistant that may not be available in CI).
import sys, importlib.util, pathlib

_ROOT = pathlib.Path(__file__).parent.parent / "custom_components" / "genia_air"

def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

const = _load("const")
LEGACY_UID_MAP = const.LEGACY_UID_MAP

# entities_catalog imports from homeassistant.components.* for type stubs
# only — we need to mock those for standalone testing.
import types

class _AttrStr:
    """Any attribute access returns the attribute name as a string."""
    def __getattr__(self, k):
        return k

for mod_name, attrs in [
    ("homeassistant.components.binary_sensor", ["BinarySensorDeviceClass"]),
    ("homeassistant.components.number",        ["NumberDeviceClass", "NumberMode"]),
    ("homeassistant.components.sensor",        ["SensorDeviceClass", "SensorStateClass"]),
]:
    parts = mod_name.split(".")
    for i in range(1, len(parts) + 1):
        full = ".".join(parts[:i])
        if full not in sys.modules:
            sys.modules[full] = types.ModuleType(full)
    for attr in attrs:
        setattr(sys.modules[mod_name], attr, _AttrStr())

ENTITIES = _load("entities_catalog").ENTITIES


def test_legacy_map_keys_unique():
    """Every new unique_id appears once."""
    keys = list(LEGACY_UID_MAP.keys())
    assert len(keys) == len(set(keys)), "Duplicate new unique_ids in LEGACY_UID_MAP"


def test_legacy_map_values_unique():
    """Every legacy unique_id maps to a single new one (no double-claim)."""
    values = [v for v in LEGACY_UID_MAP.values() if v]
    assert len(values) == len(set(values)), \
        "A legacy unique_id is claimed twice — only one new entity may adopt it"


def test_catalog_unique_ids_unique():
    """No two entities in the catalog share a unique_id."""
    uids = [e.unique_id for e in ENTITIES]
    assert len(uids) == len(set(uids))


def test_catalog_unique_ids_in_legacy_map_or_computed():
    """Every catalog entity either has a legacy uid mapping or is marked computed."""
    for e in ENTITIES:
        if e.unique_id not in LEGACY_UID_MAP:
            assert e.computed, \
                f"{e.unique_id} not in LEGACY_UID_MAP and not marked computed"
        else:
            # If listed, the legacy may be None (computed) or a string
            pass


def test_number_ranges_sane():
    """Number entities have sane min < max and a step."""
    for e in ENTITIES:
        if e.platform != "number":
            continue
        assert e.min_value is not None
        assert e.max_value is not None
        assert e.step is not None
        assert e.min_value < e.max_value, f"{e.unique_id}: min must be < max"
