"""
Unit tests for the AgentRepository / AgentFactory ports.

Demonstrates the payoff: AgentSystem roster logic can be exercised with an
in-memory fake repository — no Evennia, no ObjectDB, no tag index — and the
old ``_get_agents_fallback`` empty-list masking is gone from the system.
"""

from mygame.world.core.ports.entity_repository import AgentFactory, AgentRepository


class _Db:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeAgent:
    def __init__(self, agent_id, owner):
        self.db = _Db(agent_id=agent_id, owner=owner, role="")


class FakeAgentRepository(AgentRepository):
    """Dict-backed repository over a flat list of agents."""

    def __init__(self, agents=None):
        self.agents = list(agents or [])

    def find_agents_for_owner(self, owner):
        return [a for a in self.agents if a.db.owner is owner]

    def find_all_agents(self):
        return list(self.agents)

    def find_all_enemies(self):
        return [a for a in self.agents
                if getattr(a.db, "npc_type", None) == "enemy"]

    def find_training_buildings(self):
        return []


class TestAgentRepositoryPort:
    def test_find_agents_for_owner_filters(self):
        alice, bob = object(), object()
        repo = FakeAgentRepository([
            _FakeAgent(1, alice), _FakeAgent(2, bob), _FakeAgent(3, alice),
        ])
        alice_agents = repo.find_agents_for_owner(alice)
        assert [a.db.agent_id for a in alice_agents] == [1, 3]
        assert repo.find_agents_for_owner(bob)[0].db.agent_id == 2

    def test_find_all_agents(self):
        repo = FakeAgentRepository([_FakeAgent(1, object()), _FakeAgent(2, object())])
        assert len(repo.find_all_agents()) == 2

    def test_repository_is_abstract(self):
        try:
            AgentRepository()
        except TypeError:
            return
        raise AssertionError("AgentRepository should be abstract")

    def test_factory_is_abstract(self):
        try:
            AgentFactory()
        except TypeError:
            return
        raise AssertionError("AgentFactory should be abstract")


class TestAgentSystemUsesRepository:
    """AgentSystem.get_agents delegates to the injected repository."""

    def _make_system(self, repo):
        from mygame.world.data_registry import DataRegistry
        from mygame.world.event_bus import EventBus
        from mygame.world.systems.agent_system import AgentSystem

        return AgentSystem(DataRegistry(), EventBus(), agent_repository=repo)

    def test_get_agents_delegates_to_repository(self):
        owner = object()
        repo = FakeAgentRepository([_FakeAgent(1, owner), _FakeAgent(2, owner)])
        system = self._make_system(repo)
        assert len(system.get_agents(owner)) == 2
        assert system.get_agent_count(owner) == 2

    def test_get_agent_by_id_via_repository(self):
        owner = object()
        repo = FakeAgentRepository([_FakeAgent(7, owner)])
        system = self._make_system(repo)
        assert system.get_agent_by_id(owner, 7).db.agent_id == 7
        assert system.get_agent_by_id(owner, 99) is None
