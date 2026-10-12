from __future__ import annotations

from dataclasses import dataclass

from smart_home_agent.skills.base import Skill
from smart_home_agent.skills.external.greeting import GreetingSkill
from smart_home_agent.skills.external.general_chat import GeneralChatSkill
from smart_home_agent.skills.external.time_date import TimeDateSkill
from smart_home_agent.skills.external.web_search import WebSearchSkill
from smart_home_agent.skills.external.weather import WeatherSkill
from smart_home_agent.skills.home.device_control import DeviceControlSkill
from smart_home_agent.skills.home.device_info import DeviceInfoSkill
from smart_home_agent.skills.home.inventory import InventorySkill
from smart_home_agent.skills.home.scene import SceneSkill
from smart_home_agent.skills.system.health import HealthSkill
from smart_home_agent.skills.system.help import HelpSkill
from smart_home_agent.skills.user.access_request import AccessRequestSkill
from smart_home_agent.skills.user.permissions import PermissionsSkill


@dataclass
class SkillRegistry:
    skills: list[Skill]

    def ordered(self) -> list[Skill]:
        return list(self.skills)

    def names(self) -> list[str]:
        return [skill.name for skill in self.skills]


def build_default_registry() -> SkillRegistry:
    return SkillRegistry(
        [
            AccessRequestSkill(),
            PermissionsSkill(),
            WebSearchSkill(),
            WeatherSkill(),
            HealthSkill(),
            HelpSkill(),
            TimeDateSkill(),
            InventorySkill(),
            DeviceInfoSkill(),
            SceneSkill(),
            GreetingSkill(),
            DeviceControlSkill(),
            GeneralChatSkill(),
        ]
    )
