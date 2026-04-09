import os
import asyncio
import discord
from datetime import datetime, timedelta
from discord import app_commands
from discord.ext import commands

from utils.config import Config, logger, is_editor
from utils import database
from utils.views import PollView, PresencePollView
from utils.modals import DateModal
from utils.reminders import send_reminders, send_non_voters_biweekly_reminders

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@tree.command(name="poll", description="Créer un sondage")
@app_commands.describe(
    question="La question du sondage",
    single="Choix unique - Par défaut: non (choix multiple autorisé)",
    choix1="Premier choix (laisser vide pour un sondage Présent/Absent/En attente)",
    choix2="Deuxième choix",
    choix3="Troisième choix",
    choix4="Quatrième choix",
    choix5="Cinquième choix",
    choix6="Sixième choix",
    choix7="Septième choix",
    choix8="Huitième choix",
    choix9="Neuvième choix",
    choix10="Dixième choix",
)
async def poll_command(
    interaction: discord.Interaction,
    question: str = "Dispo?",
    single: bool = False,
    choix1: str = None, choix2: str = None, choix3: str = None, choix4: str = None,
    choix5: str = None, choix6: str = None, choix7: str = None, choix8: str = None,
    choix9: str = None, choix10: str = None
):
    """Commande pour créer un sondage"""
    choices = [choix1, choix2, choix3, choix4, choix5, choix6, choix7, choix8, choix9, choix10]
    options = [c for c in choices if c]

    if not options:
        modal = DateModal(question, [], is_presence=True, allow_multiple=False)
        await interaction.response.send_modal(modal)
        return

    if len(options) < 2:
        await interaction.response.send_message("❌ Il faut au moins 2 choix pour un sondage classique", ephemeral=True)
        return

    allow_multiple = not single
    modal = DateModal(question, options, is_presence=False, allow_multiple=allow_multiple)
    await interaction.response.send_modal(modal)


@tree.command(name="delete_poll", description="Supprimer un sondage (admin)")
@app_commands.describe(poll_id="ID du sondage à supprimer")
async def delete_poll(interaction: discord.Interaction, poll_id: int):
    """Supprime un sondage et son événement associé"""
    if not is_editor(interaction):
        await interaction.response.send_message("❌ Tu n'as pas le rôle éditeur de sondage", ephemeral=True)
        return
    
    try:
        from utils.events import delete_scheduled_event
        
        async with database.db.acquire() as conn:
            poll = await conn.fetchrow("SELECT * FROM polls WHERE id = $1", poll_id)
            
            if not poll:
                await interaction.response.send_message("❌ Sondage introuvable", ephemeral=True)
                return
            
            if poll["event_id"]:
                try:
                    await delete_scheduled_event(interaction.guild, poll["event_id"])
                except Exception as e:
                    logger.warning(f"Impossible de supprimer l'événement: {e}")
            
            await conn.execute("DELETE FROM votes WHERE poll_id = $1", poll_id)
            await conn.execute("DELETE FROM reminders_sent WHERE poll_id = $1", poll_id)
            await conn.execute("DELETE FROM polls WHERE id = $1", poll_id)
        
        await interaction.response.send_message(f"✅ Sondage {poll_id} supprimé", ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la suppression: {e}")
        await interaction.response.send_message("❌ Erreur lors de la suppression", ephemeral=True)


@tree.command(name="clean_events", description="Supprime les événements orphaned (admin)")
async def clean_events(interaction: discord.Interaction):
    """Supprime les événements Discord dont le message a été supprimé"""
    if not is_editor(interaction):
        await interaction.response.send_message("❌ Tu n'as pas le rôle éditeur de sondage", ephemeral=True)
        return
    
    await interaction.response.send_message("🔍 Recherche des événements orphaned...", ephemeral=True)
    
    try:
        from utils.events import delete_scheduled_event
        
        async with database.db.acquire() as conn:
            polls = await conn.fetch("SELECT * FROM polls WHERE event_id IS NOT NULL")
        
        removed = 0
        for poll in polls:
            if not poll["event_id"]:
                continue
            
            channel = bot.get_channel(poll["channel_id"])
            if not channel:
                try:
                    await delete_scheduled_event(interaction.guild, poll["event_id"])
                    removed += 1
                    logger.info(f"Événement {poll['event_id']} supprimé (channel introuvable)")
                except Exception as e:
                    logger.warning(f"Impossible de supprimer l'événement {poll['event_id']}: {e}")
                continue
            
            try:
                await channel.fetch_message(poll["message_id"])
            except discord.NotFound:
                try:
                    await delete_scheduled_event(interaction.guild, poll["event_id"])
                    removed += 1
                    logger.info(f"Événement {poll['event_id']} supprimé (message introuvable)")
                except Exception as e:
                    logger.warning(f"Impossible de supprimer l'événement {poll['event_id']}: {e}")
            except Exception:
                pass
        
        await interaction.edit_original_response(content=f"✅ {removed} événement(s) orphaned supprimé(s)")
        
    except Exception as e:
        logger.error(f"❌ Erreur lors du nettoyage: {e}")
        await interaction.edit_original_response(content="❌ Erreur lors du nettoyage")


@tree.command(name="check_polls", description="Vérifie l'état des sondages actifs (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def check_polls(interaction: discord.Interaction):
    """Vérifie l'état des sondages actifs"""
    try:
        async with database.db.acquire() as conn:
            polls = await conn.fetch("SELECT * FROM polls")

        if not polls:
            await interaction.response.send_message("Aucun sondage en base", ephemeral=True)
            return

        msg = "📊 **Sondages en base :**\n"
        now = datetime.now(Config.TZ)
        for p in polls:
            status = "🟢 Actif" if not p["max_date"] or p["max_date"] > now else "🔴 Fermé"
            mode = "Multiple" if p.get("allow_multiple", False) else "Unique"
            msg += f"\n{status} ID:{p['id']} - {p['question'][:40]} ({mode})"

        await interaction.response.send_message(msg, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la vérification des sondages: {e}")
        await interaction.response.send_message("❌ Erreur lors de la vérification", ephemeral=True)


async def restore_poll_views():
    """Restaure les boutons interactifs après un redémarrage"""
    await bot.wait_until_ready()

    try:
        async with database.db.acquire() as conn:
            now = datetime.now(Config.TZ)
            polls = await conn.fetch("""
                SELECT * FROM polls 
                WHERE max_date IS NULL OR max_date > $1
            """, now)

        logger.info(f"🔄 Restauration de {len(polls)} sondages actifs...")

        restored = 0
        for poll in polls:
            try:
                channel = bot.get_channel(poll["channel_id"])
                if not channel:
                    continue

                try:
                    message = await channel.fetch_message(poll["message_id"])
                except discord.NotFound:
                    logger.warning(f"Message {poll['message_id']} introuvable, suppression du sondage {poll['id']}")
                    async with database.db.acquire() as conn:
                        await conn.execute("DELETE FROM polls WHERE id=$1", poll["id"])
                    continue

                if poll["is_presence_poll"]:
                    view = PresencePollView(poll["id"], show_edit=Config.EDITOR_ROLE_ID is not None)
                else:
                    view = PollView(poll["id"], poll["options"], poll["allow_multiple"], show_edit=Config.EDITOR_ROLE_ID is not None)
                
                await message.edit(view=view)
                restored += 1

            except Exception as e:
                logger.error(f"❌ Erreur lors de la restauration du sondage {poll['id']}: {e}")

        logger.info(f"✅ {restored}/{len(polls)} sondages restaurés avec succès")
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la restauration des vues: {e}")


async def reminder_scheduler():
    """Scheduler qui vérifie régulièrement s'il faut envoyer des rappels"""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            await send_reminders()
        except Exception as e:
            logger.error(f"❌ Erreur dans le scheduler de rappels: {e}")

        await asyncio.sleep(Config.REMINDER_CHECK_INTERVAL)


async def daily_19h_scheduler():
    """Scheduler qui s'exécute tous les jours à 19h"""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            now = datetime.now(Config.TZ)
            
            target = now.replace(hour=Config.DAILY_REMINDER_HOUR, minute=0, second=0, microsecond=0)
            if now.hour >= Config.DAILY_REMINDER_HOUR:
                target += timedelta(days=1)
            
            wait_seconds = (target - now).total_seconds()
            logger.info(f"⏰ Prochain rappel quotidien dans {wait_seconds/3600:.1f}h ({target})")
            await asyncio.sleep(wait_seconds)
            
            await send_non_voters_biweekly_reminders()
            
        except Exception as e:
            logger.error(f"❌ Erreur dans le scheduler quotidien: {e}")
            await asyncio.sleep(3600)


@bot.event
async def on_ready():
    try:
        database.db = await database.get_db()
        database.bot = bot
        await database.init_db()
        await tree.sync()

        await restore_poll_views()

        bot.loop.create_task(reminder_scheduler())
        bot.loop.create_task(daily_19h_scheduler())

        logger.info(f"✅ Bot connecté : {bot.user}")
    except Exception as e:
        logger.error(f"❌ Erreur critique lors du démarrage: {e}")
        raise


if __name__ == "__main__":
    token = os.getenv("TOKEN_DISCORD")
    if not token:
        logger.error("❌ TOKEN_DISCORD non défini dans les variables d'environnement")
        exit(1)
    
    bot.run(token)