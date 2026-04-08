import discord
from discord.ui import Modal, TextInput
from datetime import datetime
from utils.config import Config
import logging

logger = logging.getLogger(__name__)


class DateModal(Modal, title="📅 Dates de l'événement"):
    """Modal pour saisir les dates du sondage"""
    
    event_date = TextInput(
        label="Date événement",
        placeholder="Ex: 25/12/2024 ou 25/12/2024-20:00",
        required=True,
        max_length=16
    )

    max_date = TextInput(
        label="Date limite de vote (optionnel)",
        placeholder="Ex: 24/12/2024-18:00",
        required=False,
        max_length=16
    )

    def __init__(self, question: str, options: list, is_presence: bool, allow_multiple: bool = False):
        super().__init__()
        self.question = question
        self.options = options
        self.is_presence = is_presence
        self.allow_multiple = allow_multiple

    async def on_submit(self, interaction: discord.Interaction):
        try:
            event_dt = self._parse_date(self.event_date.value.strip())
            max_dt = self._parse_date(self.max_date.value.strip()) if self.max_date.value.strip() else None

            validation_error = self._validate_dates(event_dt, max_dt)
            if validation_error:
                await interaction.response.send_message(validation_error, ephemeral=True)
                return

            from utils.poll_utils import create_poll
            await create_poll(interaction, self.question, self.options, self.is_presence, 
                            event_dt, max_dt, self.allow_multiple)

        except ValueError as e:
            logger.warning(f"Format de date invalide: {e}")
            await interaction.response.send_message(
                "❌ Format de date invalide. Utilisez JJ/MM/AAAA ou JJ/MM/AAAA-HH:mm",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"❌ Erreur lors de la soumission du modal: {e}")
            await interaction.response.send_message("❌ Une erreur est survenue", ephemeral=True)

    def _parse_date(self, date_str: str) -> datetime:
        """Parse une date au format JJ/MM/AAAA ou JJ/MM/AAAA-HH:mm"""
        if "-" in date_str:
            return datetime.strptime(date_str, "%d/%m/%Y-%H:%M").replace(tzinfo=Config.TZ)
        else:
            return datetime.strptime(date_str, "%d/%m/%Y").replace(hour=0, minute=0, tzinfo=Config.TZ)

    def _validate_dates(self, event_dt: datetime, max_dt: datetime = None) -> str:
        """Valide les dates saisies. Retourne un message d'erreur ou None"""
        now = datetime.now(Config.TZ)
        
        if event_dt < now:
            return "❌ La date de l'événement ne peut pas être dans le passé"
        
        if (event_dt - now).days > Config.MAX_EVENT_DAYS_AHEAD:
            return f"❌ L'événement ne peut pas être dans plus de {Config.MAX_EVENT_DAYS_AHEAD} jours"
        
        if max_dt:
            if max_dt < now:
                return "❌ La date limite ne peut pas être dans le passé"
            if max_dt > event_dt:
                return "❌ La date limite doit être avant la date de l'événement"
        
        return None