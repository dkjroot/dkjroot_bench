import json
import re
from hashlib import sha256
from uuid import uuid4

from dojo.config import MAX_AGENTS, agent_limits, validate_rounds
from dojo.profiles import PROFILE_ID
from dojo.storage import Record, write_private_json

SPRITES = ("rocket", "rover", "diamond", "dome", "walker", "glider", "shield", "twin-pod")
DEFAULT_NAMES = ("Ann", "Ben", "Cat", "Dug", "Egg", "Fax", "Guy", "Han")
BADGE = re.compile(r"[A-Za-z0-9]")


def default_characters(agents):
    return {id: {"name": name, "badge": name[0], "sprite": sprite}
            for id, name, sprite in zip(agents, DEFAULT_NAMES, SPRITES)}


def name_field(value, field, limit):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or not value.strip().isprintable():
        raise ValueError(f"Enter a {field} of up to {limit} characters.")
    return value.strip()


def team_revision(team):
    return sha256(json.dumps(team, sort_keys=True).encode()).hexdigest()


class Teams:
    def __init__(self, path, profiles):
        self.path = path
        self.profiles = profiles

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                teams = json.load(source)
            if not isinstance(teams, dict):
                raise ValueError
            for id, team in teams.items():
                if not PROFILE_ID.fullmatch(id) or not isinstance(team, dict):
                    raise ValueError
                self._team(team)
            return teams
        except FileNotFoundError:
            return {}
        except ValueError as error:
            raise OSError("Could not read team profiles.") from error

    @staticmethod
    def _team(data):
        if not isinstance(data, dict) or data.keys() - {"id", "name", "agents", "rounds"}:
            raise ValueError("Expected team profile settings.")
        name = name_field(data.get("name"), "team name", 60)
        rounds = data.get("rounds")
        if rounds is not None:
            validate_rounds(rounds)
        agents = data.get("agents")
        if not isinstance(agents, list) or not 1 <= len(agents) <= MAX_AGENTS:
            raise ValueError(f"Choose between 1 and {MAX_AGENTS} agents.")
        roster = []
        for agent in agents:
            if not isinstance(agent, dict) or agent.keys() != {"name", "badge", "sprite", "profile_id"}:
                raise ValueError("Each agent needs a name, character, sprite, and model profile.")
            name_value = name_field(agent["name"], "agent name", 40)
            badge = agent["badge"]
            if not isinstance(badge, str) or not BADGE.fullmatch(badge):
                raise ValueError("Use one letter or number for each character.")
            if agent["sprite"] not in SPRITES:
                raise ValueError("Choose an available sprite.")
            if not isinstance(agent["profile_id"], str) or not PROFILE_ID.fullmatch(agent["profile_id"]):
                raise ValueError("Choose a model profile for each agent.")
            roster.append({**agent, "name": name_value})
        return {"name": name, "rounds": rounds, "agents": roster}

    @staticmethod
    def public(id, team, enabled_profiles):
        available = [index for index, agent in enumerate(team["agents"]) if agent["profile_id"] in enabled_profiles]
        return {"id": id, **team, "enabled": len(available) == len(team["agents"]),
                "available_agents": available, "revision": team_revision(team)}

    def list(self):
        enabled = {profile["id"] for profile in self.profiles.list() if profile["enabled"]}
        return [self.public(id, team, enabled) for id, team in self._read().items()]

    def get(self, id):
        if not isinstance(id, str) or not PROFILE_ID.fullmatch(id):
            raise ValueError("Choose a team profile.")
        team = self._read().get(id)
        if team is None:
            raise ValueError("That team profile no longer exists.")
        return team

    def select(self, id, level, squad=None, revision=None):
        team = self.get(id)
        if revision is not None and revision != team_revision(team):
            raise ValueError("The team roster changed. Refresh it and choose your squad again.")
        roster = team["agents"]
        minimum, maximum = agent_limits(level)
        if squad is None:
            enabled = {profile["id"] for profile in self.profiles.list() if profile["enabled"]}
            available = [index for index, agent in enumerate(roster) if agent["profile_id"] in enabled]
            if len(available) < minimum and len(roster) >= minimum:
                raise ValueError("Add API keys to enough of this team's model profiles to run the puzzle.")
            squad = available[:min(max(level.DEFAULT_AGENTS, minimum), maximum)]
        if (not isinstance(squad, list)
                or any(type(index) is not int or not 0 <= index < len(roster) for index in squad)
                or len(set(squad)) != len(squad)):
            raise ValueError("Choose distinct agents from the team roster.")
        if minimum == maximum and len(squad) != minimum:
            unit = "agent" if minimum == 1 else "agents"
            raise ValueError(f"{level.TITLE} requires {minimum} {unit}.")
        if not minimum <= len(squad) <= maximum:
            raise ValueError(f"{level.TITLE} needs a squad of {minimum} to {maximum} agents.")
        return {**team, "agents": [roster[index] for index in squad]}

    def save(self, data):
        team = self._team(data)
        id = data.get("id")
        if "id" in data and (not isinstance(id, str) or not PROFILE_ID.fullmatch(id)):
            raise ValueError("Choose a team profile.")
        profiles = {profile["id"]: profile["enabled"] for profile in self.profiles.list()}
        with self._lock():
            entries = self._read()
            if id and id not in entries:
                raise ValueError("That team profile no longer exists.")
            previous_ids = {agent["profile_id"] for agent in entries.get(id, {}).get("agents", [])}
            if any(agent["profile_id"] not in profiles and agent["profile_id"] not in previous_ids
                   for agent in team["agents"]):
                raise ValueError("Choose an available model profile for each agent.")
            id = id or uuid4().hex
            entries[id] = team
            write_private_json(self.path, entries)
        return self.public(id, team, {id for id, enabled in profiles.items() if enabled})

    def delete(self, id):
        if not isinstance(id, str) or not PROFILE_ID.fullmatch(id):
            raise ValueError("Choose a team profile.")
        with self._lock():
            entries = self._read()
            if id not in entries:
                raise ValueError("That team profile no longer exists.")
            del entries[id]
            write_private_json(self.path, entries)

    def _lock(self):
        try:
            return Record.acquire(self.path.parent, self.path.name + ".lock")
        except BlockingIOError as error:
            raise BlockingIOError("Another viewer is saving a team profile. Try again.") from error
