"""Cross-planet relocation helpers shared by admin and player travel commands.

Both the admin commands ('goto', 'transfer') and the player travel commands
('launch', 'recall') need the same two spatial primitives:

* :func:`resolve_planet_room` — find the destination planet's one PlanetRoom.
* :func:`relocate_object` — move an entity into that room at specific coords,
  with the coordinate-index bookkeeping a cross-planet hop requires.

They live here (a neutral adapter module) so command modules don't have to
import from each other — ``game_commands`` importing ``admin_commands`` just
to reach a shared helper inverted the dependency direction.
"""


def resolve_planet_room(caller, planet):
    """Return the shared PlanetRoom for *planet*, or None (after messaging).

    The single lookup shared by teleport ('goto'), transfer, launch, and
    recall: all need the destination planet's one PlanetRoom to relocate an
    object into. Messages the caller on any failure so callers just bail on
    None.
    """
    planet_rooms = None
    try:
        from server.conf.game_init import game_systems
        planet_rooms = game_systems.get("planet_rooms", {})
    except (ImportError, AttributeError):
        pass

    if not planet_rooms:
        caller.msg("Planet rooms not available.")
        return None

    target_room = planet_rooms.get(planet)
    if not target_room:
        caller.msg(f"No PlanetRoom found for {planet}.")
        return None
    return target_room


def relocate_object(obj, target_room, tx, ty, planet):
    """Relocate *obj* to ``(tx, ty, planet)`` within/into *target_room*.

    The shared spatial move behind 'goto' (relocating the caller), 'transfer'
    (pulling another entity to the caller's tile), 'launch', and 'recall'.
    Handles the cross-planet PlanetRoom move plus coordinate-index
    bookkeeping. Does NOT message or look — that is the caller's concern,
    since who-sees-what differs between moving yourself and summoning someone
    else.

    move_hooks=False on the cross-planet move_to: Evennia's arrival hooks
    (at_object_receive + the auto-look via at_post_move) fire DURING move_to —
    before move_entity sets the new x/y below — so they'd render/react at the
    STALE origin coords. We do the index bookkeeping ourselves instead.

    notify=False on move_entity: a teleport/summon is not a step onto an
    adjacent tile; for a cross-planet move the stored old coords belong to the
    origin planet, so arrival/departure messaging would notify the wrong
    players.
    """
    origin_room = obj.location
    old_x = getattr(obj.db, "coord_x", None)
    old_y = getattr(obj.db, "coord_y", None)

    obj.db.coord_planet = planet

    if obj.location is not target_room:
        # Skipping at_object_leave means the origin room's coordinate index
        # still holds the object — remove it explicitly so it doesn't leak.
        if origin_room is not None and old_x is not None and old_y is not None:
            idx = getattr(getattr(origin_room, "ndb", None), "_coord_index", None)
            if idx is not None:
                try:
                    idx.remove(obj, int(old_x), int(old_y))
                except Exception:  # pragma: no cover - defensive
                    pass
        obj.move_to(target_room, quiet=True, move_hooks=False)

    target_room.move_entity(obj, tx, ty, notify=False)
