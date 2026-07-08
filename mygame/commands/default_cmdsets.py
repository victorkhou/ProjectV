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
    CmdMove, CmdHarvest, CmdBuild, CmdUpgrade, CmdDemolish,
    CmdAttack, CmdEquip, CmdUnequip, CmdUse, CmdThrow, CmdReload,
    CmdDeposit, CmdWithdraw,
    CmdResearch, CmdPowerup,
    CmdScore, CmdEquipment, CmdBuildings, CmdScan, CmdTechnology,
    CmdInventory, CmdChat, CmdMessage, CmdSay, CmdLook, CmdMap,
    CmdLeave, CmdEnter, CmdCloseExit, CmdOpenExit, CmdStop, CmdWho, CmdGet,
)
from commands.agent_commands import CmdAgent
from commands.admin_commands import (
    CmdReboot, CmdPurgeRooms, CmdTeleport, CmdClearFog, CmdMigrate,
    CmdAdminBuilding, CmdAdminAgent, CmdAdminResource, CmdAdminPlayer,
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
        # Remove Evennia built-ins replaced by game commands
        from evennia.commands.default.general import CmdWhisper
        self.remove(CmdWhisper)
        # Remove the room-exit builder CmdOpen (key "@open"). Our world is
        # coordinate-based (one PlanetRoom per planet, no room-to-room exits),
        # so it is meaningless here — and because CMD_IGNORE_PREFIXES makes
        # "@open" match a bare "open", leaving it would shadow the "open"
        # alias on CmdOpenExit. CmdCloseExit's "close" alias has no builtin.
        from evennia.commands.default.building import CmdOpen
        self.remove(CmdOpen)
        # Game commands
        self.add(CmdMove())
        self.add(CmdHarvest())
        self.add(CmdBuild())
        self.add(CmdUpgrade())
        self.add(CmdDemolish())
        self.add(CmdAttack())
        self.add(CmdEquip())
        self.add(CmdUnequip())
        self.add(CmdUse())
        self.add(CmdThrow())
        self.add(CmdReload())
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
        self.add(CmdMap())
        self.add(CmdLeave())
        self.add(CmdEnter())
        self.add(CmdStop())
        self.add(CmdCloseExit())
        self.add(CmdOpenExit())
        # Agent commands
        self.add(CmdAgent())
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
        self.add(CmdAdminPlayer())
        # Override Evennia's default who with rank/level display
        self.add(CmdWho())


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
