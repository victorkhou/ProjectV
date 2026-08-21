"""
Module-level spawn prototypes (Evennia reads this via ``PROTOTYPE_MODULES``).

Intentionally empty: this project creates objects through its own data-driven
paths rather than Evennia prototypes. Buildings, items, and agents are defined
in ``mygame/data/definitions/*.yaml``, loaded by
:class:`world.data_registry.DataRegistry`, and instantiated by the factory
helpers in ``typeclasses.objects`` (``create_game_item``, ``spawn_gear_drop``,
``spawn_resource_drop``) wired at the composition root.

The module must remain importable for Evennia's prototype loader. Add a
prototype dict here only if something needs the ``spawn``/``olc`` workflow; see
the Evennia prototype docs for the accepted keys.
"""
