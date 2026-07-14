"""
Command sets

All commands in the game must be grouped in a cmdset.  A given command
can be part of any number of cmdsets and cmdsets can be added/removed
and merged onto entities at runtime.

To create new commands to populate the cmdset, see
`commands/game_commands.py`.

This module wraps the default command sets of Evennia; overloads them
to add/remove commands from the default lineup. You can create your
own cmdsets by inheriting from them or directly from `evennia.CmdSet`.

"""

from evennia import default_cmds

from commands.game_commands import (
    CmdMove, CmdHarvest, CmdBuild, CmdUpgrade, CmdDemolish, CmdRepair,
    CmdAttack, CmdTarget, CmdShoot,
    CmdEquip, CmdUnequip, CmdUse, CmdThrow, CmdReload, CmdCraft,
    CmdSetFuse, CmdArm,
    CmdDeposit, CmdWithdraw,
    CmdResearch, CmdPowerup,
    CmdScore, CmdEquipment, CmdBuildings, CmdScan, CmdTechnology,
    CmdInventory, CmdChat, CmdMessage, CmdSay, CmdLook, CmdMap,
    CmdLeave, CmdEnter, CmdCloseExit, CmdOpenExit, CmdExit, CmdStop, CmdWho, CmdGet,
    CmdDrop, CmdSell, CmdJunk,
)
from commands.agent_commands import (
    CmdAgent,
    CmdTrain,
    CmdAssign,
    CmdUnassign,
)
from commands.lifecycle_commands import (
    CmdClass,
    CmdSpawn,
    CmdDeploy,
)
from commands.admin_commands import (
    CmdReboot, CmdPurgeRooms, CmdTeleport, CmdClearFog, CmdMigrate,
    CmdAdminBuilding, CmdAdminAgent, CmdAdminResource, CmdAdminItem,
    CmdAdminPlayer, CmdAdminOutpost,
)


class CharacterCmdSet(default_cmds.CharacterCmdSet):
    """
    The `CharacterCmdSet` contains general in-game commands like `look`,
    `get`, etc available on in-game Character objects. It is merged with
    the `AccountCmdSet` when an Account puppets a Character.
    """

    key = "DefaultCharacter"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        # ------------------------------------------------------------------ #
        #  Prune stock Evennia commands that don't fit this game's model.
        #
        #  This world is coordinate-based: one PlanetRoom per planet, with no
        #  room-to-room exit graph, and all in-world objects (buildings, items,
        #  agents) are typed and registered in a coordinate index by the game's
        #  own spawn commands (@building / @item / @agent). Stock builder
        #  commands that assume a room graph, or that create untyped/unindexed
        #  objects, produce broken or orphaned state here, so we remove them.
        # ------------------------------------------------------------------ #
        from evennia.commands.default.general import (
            CmdWhisper, CmdPose, CmdSetDesc, CmdGive, CmdHome, CmdDrop as CmdStockDrop,
        )
        from evennia.commands.default.building import (
            CmdOpen, CmdDig, CmdTunnel, CmdLink, CmdUnLink, CmdSetHome,
            CmdCreate, CmdSpawn, CmdCopy, CmdTeleport as CmdBuiltinTeleport,
        )

        # Replaced by game commands.
        self.remove(CmdWhisper)  # replaced by CmdMessage (page/tell)
        # Room-exit builder CmdOpen (key "@open"): CMD_IGNORE_PREFIXES makes
        # "@open" match a bare "open", so leaving it would shadow CmdOpenExit's
        # "open" alias. (CmdCloseExit's "close" alias has no stock conflict.)
        self.remove(CmdOpen)
        # Stock @teleport/@tel is a room-based teleport with the SAME key and
        # alias as our coordinate CmdTeleport — remove it so ours is the only
        # match (previously it was silently shadowed).
        self.remove(CmdBuiltinTeleport)

        # Room-graph builders — meaningless without room-to-room exits.
        self.remove(CmdDig)      # @dig: create a room + exits to it
        self.remove(CmdTunnel)   # @tunnel: dig in a compass direction
        self.remove(CmdLink)     # @link: link exits between rooms
        self.remove(CmdUnLink)   # unlink: remove exit links
        self.remove(CmdSetHome)  # @sethome: set an object's home room
        self.remove(CmdHome)     # home: teleport to your home room

        # Generic object builders — bypass the typed, coordinate-indexed spawn
        # path (@building / @item / @agent), producing orphaned objects.
        self.remove(CmdCreate)   # @create: make an untyped object
        self.remove(CmdSpawn)    # @spawn/@olc: prototype spawner
        self.remove(CmdCopy)     # @copy: duplicate an object (unindexed clone)

        # RP/social flavor — not part of the RTS combat loop.
        self.remove(CmdPose)     # pose/emote (":")
        self.remove(CmdSetDesc)  # setdesc: set your character description
        self.remove(CmdGive)     # give: hand an object to another character

        # Stock 'drop' moves an item to the room WITHOUT setting coord_x/coord_y
        # or registering it in the coordinate index (its at_drop runs after
        # at_object_receive already skipped indexing), so dropped items were
        # invisible to get/scan/look. Replaced by the coordinate-aware CmdDrop.
        self.remove(CmdStockDrop)

        # Game commands
        self.add(CmdMove())
        self.add(CmdHarvest())
        self.add(CmdBuild())
        self.add(CmdUpgrade())
        self.add(CmdDemolish())
        self.add(CmdRepair())
        self.add(CmdAttack())
        self.add(CmdTarget())
        self.add(CmdShoot())
        self.add(CmdEquip())
        self.add(CmdUnequip())
        self.add(CmdUse())
        self.add(CmdThrow())
        self.add(CmdSetFuse())
        self.add(CmdArm())
        self.add(CmdReload())
        self.add(CmdCraft())
        self.add(CmdDeposit())
        self.add(CmdWithdraw())
        self.add(CmdResearch())
        self.add(CmdPowerup())
        self.add(CmdScore())
        self.add(CmdEquipment())
        self.add(CmdBuildings())
        self.add(CmdScan())
        self.add(CmdTechnology())
        self.add(CmdInventory())
        self.add(CmdChat())
        self.add(CmdMessage())
        self.add(CmdSay())
        self.add(CmdLook())
        self.add(CmdGet())
        self.add(CmdDrop())
        self.add(CmdSell())
        self.add(CmdJunk())
        self.add(CmdMap())
        self.add(CmdLeave())
        self.add(CmdEnter())
        self.add(CmdStop())
        self.add(CmdCloseExit())
        self.add(CmdOpenExit())
        self.add(CmdExit())
        # Agent commands
        self.add(CmdAgent())
        self.add(CmdTrain())
        self.add(CmdAssign())
        self.add(CmdUnassign())
        # Admin commands (lock-gated to Builder+)
        self.add(CmdReboot())
        self.add(CmdPurgeRooms())
        self.add(CmdTeleport())
        self.add(CmdClearFog())
        self.add(CmdMigrate())
        # Admin routers (replace old standalone admin commands)
        self.add(CmdAdminBuilding())
        self.add(CmdAdminAgent())
        self.add(CmdAdminResource())
        self.add(CmdAdminItem())
        self.add(CmdAdminPlayer())
        self.add(CmdAdminOutpost())
        # Override Evennia's default who with rank/level display
        self.add(CmdWho())
        # Player lifecycle (spawning/lobby) commands. Harmless when the lobby
        # flow is disabled: 'class'/'spawn'/'deploy' just report you can only
        # use them while preparing to deploy (state != SPAWNING/LOBBY).
        self.add(CmdClass())
        self.add(CmdSpawn())
        self.add(CmdDeploy())


class AccountCmdSet(default_cmds.AccountCmdSet):
    """
    This is the cmdset available to the Account at all times. It is
    combined with the `CharacterCmdSet` when the Account puppets a
    Character. It holds game-account-specific commands, channel
    commands, etc.
    """

    key = "DefaultAccount"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        # Remove Evennia's built-in page/tell — replaced by CmdMessage
        from evennia.commands.default.comms import CmdPage
        self.remove(CmdPage)
        # Override quit so a CLEAN quit marks the puppet (clean-vs-linkdead
        # signal for the lobby lifecycle flow). Harmless when the flow is off:
        # it just sets a transient marker and delegates to the stock quit.
        try:
            from commands.lifecycle_commands import CmdQuit as _CmdQuit
            if _CmdQuit is not None:
                self.add(_CmdQuit())
        except Exception:
            pass


class UnloggedinCmdSet(default_cmds.UnloggedinCmdSet):
    """
    Command set available to the Session before being logged in.  This
    holds commands like creating a new account, logging in, etc.
    """

    key = "DefaultUnloggedin"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        #
        # any commands you add below will overload the default ones.
        #


class SessionCmdSet(default_cmds.SessionCmdSet):
    """
    This cmdset is made available on Session level once logged in. It
    is empty by default.
    """

    key = "DefaultSession"

    def at_cmdset_creation(self):
        """
        This is the only method defined in a cmdset, called during
        its creation. It should populate the set with command instances.

        As and example we just add the empty base `Command` object.
        It prints some info.
        """
        super().at_cmdset_creation()
        #
        # any commands you add below will overload the default ones.
        #
