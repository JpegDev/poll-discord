import discord
import logging

logger = logging.getLogger(__name__)


async def create_scheduled_event(guild: discord.Guild, poll: dict, channel_name: str = None) -> int:
    """Crée un événement Discord planifié à partir d'un sondage de présence"""
    try:
        if channel_name:
            event_name = f"#{channel_name}"
        else:
            event_name = poll["question"][:100]
        
        event_start = poll["event_date"]
        event_end = event_start + timedelta(hours=2)
        
        description = f"Sondage créé via bot Discord\nDate limite de vote: {poll['max_date'].strftime('%d/%m/%Y') if poll['max_date'] else 'Aucune'}"
        
        event = await guild.create_scheduled_event(
            name=event_name,
            description=description,
            start=event_start,
            end=event_end,
            location="Sondage Discord",
            privacy_level=discord.ScheduledEventPrivacyLevel.guild_only
        )
        
        logger.info(f"✅ Événement créé: {event.id} - {event_name}")
        return event.id
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la création de l'événement: {e}")
        return None


async def delete_scheduled_event(guild: discord.Guild, event_id: int):
    """Supprime un événement Discord planifié"""
    try:
        event = await guild.fetch_scheduled_event(event_id)
        await event.delete()
        logger.info(f"✅ Événement supprimé: {event_id}")
    except discord.NotFound:
        logger.warning(f"Événement {event_id} introuvable")
    except Exception as e:
        logger.error(f"❌ Erreur lors de la suppression de l'événement {event_id}: {e}")


from datetime import timedelta