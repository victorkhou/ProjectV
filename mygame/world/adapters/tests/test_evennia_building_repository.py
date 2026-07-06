"""
Unit tests for the Evennia building/movement adapters' filtering logic.

These cover the behavior-preservation filters that the port extraction must
keep — the ``movement_queue`` truthiness filter (restart recovery of in-flight
NPCs) and the ``training_agent_id is not None`` filter (training-building
recovery) — by injecting fake Evennia query modules, so the real adapter code
(not just a fake) is exercised.
"""

import sys
import types

from mygame.world.adapters.evennia_building_repository import (
    EvenniaMovingEntityRepository,
)
from mygame.world.adapters.evennia_agent_repository import EvenniaAgentRepository


class _Db:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Npc:
    def __init__(self, queue):
        self.db = _Db(movement_queue=queue)


def _install_fake_search(objects):
    """Install a fake evennia.utils.search returning *objects* for any tag."""
    prev = sys.modules.get("evennia.utils.search")
    mod = types.ModuleType("evennia.utils.search")
    mod.search_object_by_tag = lambda tag, category=None: list(objects)
    sys.modules["evennia.utils.search"] = mod
    return prev


def _restore(name, prev):
    if prev is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = prev


class TestFindMovingNpcs:
    def test_only_non_empty_queues_returned(self):
        moving = _Npc([(1, 1)])
        idle = _Npc([])
        none_q = _Npc(None)
        prev = _install_fake_search([moving, idle, none_q])
        try:
            result = EvenniaMovingEntityRepository().find_moving_npcs()
        finally:
            _restore("evennia.utils.search", prev)
        assert result == [moving]

    def test_query_failure_returns_empty(self):
        # No evennia.utils.search installed → import inside the method raises,
        # adapter must degrade to [] rather than propagate.
        prev = sys.modules.pop("evennia.utils.search", None)
        # Force the lazy import to fail by installing a module without the attr.
        broken = types.ModuleType("evennia.utils.search")
        sys.modules["evennia.utils.search"] = broken
        try:
            assert EvenniaMovingEntityRepository().find_moving_npcs() == []
        finally:
            _restore("evennia.utils.search", prev)


class _Building:
    def __init__(self, training_agent_id):
        self._val = training_agent_id

    class _Attrs:
        def __init__(self, val):
            self._val = val

        def get(self, key):
            return self._val

    @property
    def attributes(self):
        return self._Attrs(self._val)


def _install_fake_objectdb(rows):
    """Install a fake evennia.objects.models.ObjectDB.objects.filter -> rows."""
    prev_models = sys.modules.get("evennia.objects.models")

    class _QS:
        def __init__(self, data):
            self._data = data

        def __iter__(self):
            return iter(self._data)

    class _Manager:
        def filter(self, **kw):
            return _QS(rows)

    class _ObjectDB:
        objects = _Manager()

    mod = types.ModuleType("evennia.objects.models")
    mod.ObjectDB = _ObjectDB
    sys.modules["evennia.objects.models"] = mod
    return prev_models


class TestFindTrainingBuildings:
    def test_none_training_id_excluded(self):
        has_id = _Building(training_agent_id=7)
        cleared = _Building(training_agent_id=None)
        prev = _install_fake_objectdb([has_id, cleared])
        try:
            result = EvenniaAgentRepository().find_training_buildings()
        finally:
            _restore("evennia.objects.models", prev)
        assert result == [has_id]
