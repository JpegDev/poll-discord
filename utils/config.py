import os
import logging
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Config:
    """Configuration centralisée du bot"""
    TZ = ZoneInfo("Europe/Paris")
    EMOJIS = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮", "🇯",
              "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷", "🇸", "🇹"]
    MAX_OPTIONS = 20
    MAX_CONTENT_LENGTH = 2000
    MAX_MENTIONS_DISPLAY = 20
    REMINDER_CHECK_INTERVAL = 3600
    DAILY_REMINDER_HOUR = 19
    MAX_EVENT_DAYS_AHEAD = 730
    
    REMINDER_J_MINUS_2_MIN = 47
    REMINDER_J_MINUS_2_MAX = 49
    REMINDER_J_MINUS_1_MIN = 23
    REMINDER_J_MINUS_1_MAX = 25
    
    EDITOR_ROLE_ID = int(os.getenv("EDITOR_ROLE_ID", "0")) or None


def is_editor(interaction) -> bool:
    """Vérifie si l'utilisateur a le rôle éditeur de sondage"""
    if Config.EDITOR_ROLE_ID is None:
        return False
    return any(role.id == Config.EDITOR_ROLE_ID for role in interaction.user.roles)