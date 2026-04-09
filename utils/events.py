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
            start_time=event_start,
            end_time=event_end,
            location="Sondage Discord",
            privacy_level=discord.PrivacyLevel.guild_only,
            entity_type=discord.EntityType.external
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


async def check_event_exists(guild: discord.Guild, event_id: int) -> bool:
    """Vérifie si un événement existe toujours"""
    try:
        await guild.fetch_scheduled_event(event_id)
        return True
    except discord.NotFound:
        return False
    except Exception:
        return False


async def clear_orphaned_event_id(guild: discord.Guild, event_id: int) -> bool:
    """Vérifie si l'événement existe et clears l'event_id si supprimé manuellement"""
    exists = await check_event_exists(guild, event_id)
    if not exists:
        logger.warning(f"Événement {event_id} supprimé manuellement, nettoyage de la base...")
        return True
    return False


from datetime import timedelta