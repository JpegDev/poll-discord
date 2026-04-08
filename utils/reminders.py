import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
import discord

from utils.config import Config
from utils import database
from utils.poll_utils import update_poll_display

logger = logging.getLogger(__name__)


async def send_reminders():
    """Collecte les rappels par utilisateur et envoie un DM groupé à chacun"""
    try:
        now = datetime.now(Config.TZ)

        async with database.db.acquire() as conn:
            polls = await conn.fetch("SELECT * FROM polls")

        user_reminders = defaultdict(list)

        for poll in polls:
            try:
                if poll["max_date"] and now >= poll["max_date"]:
                    await close_poll(poll)
                    continue

                channel = database.bot.get_channel(poll["channel_id"])
                if not channel:
                    continue

                try:
                    message = await channel.fetch_message(poll["message_id"])
                except discord.NotFound:
                    logger.warning(f"Message {poll['message_id']} introuvable pour le sondage {poll['id']}")
                    continue
                except Exception:
                    continue

                guild = channel.guild
                async with database.db.acquire() as conn:
                    votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll["id"])
                voted_user_ids = {v["user_id"] for v in votes}
                waiting_user_ids = {v["user_id"] for v in votes if v["emoji"] == "⏳"}

                event_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
                deadline_str = f" | ⏰ Limite : {poll['max_date'].strftime('%d/%m/%Y à %H:%M')}" if poll["max_date"] else ""
                poll_summary = f"#{channel.name} — {poll['question']} | 📅 {event_str}{deadline_str} | 👉 {message.jump_url}"

                if poll["max_date"]:
                    time_until_deadline = poll["max_date"] - now
                    if timedelta(hours=Config.REMINDER_J_MINUS_2_MIN) <= time_until_deadline <= timedelta(hours=Config.REMINDER_J_MINUS_2_MAX):
                        if not await _reminder_already_sent(poll["id"], 'j_minus_2_waiting'):
                            for uid in waiting_user_ids:
                                user_reminders[uid].append(f"⏰ **Plus que 2 jours !** — {poll_summary}")
                            await _mark_reminder_sent(poll["id"], 'j_minus_2_waiting')
                            logger.info(f"📨 Rappel J-2 marqué pour le sondage {poll['id']}")
                    elif timedelta(hours=Config.REMINDER_J_MINUS_1_MIN) <= time_until_deadline <= timedelta(hours=Config.REMINDER_J_MINUS_1_MAX):
                        if not await _reminder_already_sent(poll["id"], 'j_minus_1_waiting'):
                            for uid in waiting_user_ids:
                                user_reminders[uid].append(f"🔔 **Dernier jour !** — {poll_summary}")
                            await _mark_reminder_sent(poll["id"], 'j_minus_1_waiting')
                            logger.info(f"📨 Rappel J-1 marqué pour le sondage {poll['id']}")
                else:
                    if poll["is_presence_poll"]:
                        poll_age = now - poll["created_at"].replace(tzinfo=Config.TZ)
                        weeks_since_creation = int(poll_age.days / 7)
                        for week in range(1, weeks_since_creation + 1):
                            if not await _reminder_already_sent(poll["id"], f"weekly_waiting_{week}"):
                                target_week_date = poll["created_at"].replace(tzinfo=Config.TZ) + timedelta(weeks=week)
                                if target_week_date.date() == now.date() and 18 <= now.hour <= 20:
                                    for uid in waiting_user_ids:
                                        user_reminders[uid].append(f"📅 **Rappel hebdomadaire** — {poll_summary}")
                                    await _mark_reminder_sent(poll["id"], f"weekly_waiting_{week}")
                                    logger.info(f"📨 Rappel hebdo semaine {week} marqué pour le sondage {poll['id']}")

            except Exception as e:
                logger.error(f"❌ Erreur lors du traitement des rappels pour le sondage {poll['id']}: {e}")

        await _send_grouped_reminders(user_reminders, "⏰ **Rappels — Sondages en attente de confirmation**")

    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi des rappels: {e}")


async def _send_grouped_reminders(user_reminders, title):
    """Envoie un DM groupé à chaque utilisateur avec tous ses rappels"""
    for user_id, reminders in user_reminders.items():
        if not reminders:
            continue
        member = None
        for guild in database.bot.guilds:
            member = guild.get_member(user_id)
            if member:
                break
        if not member or member.bot:
            continue
        try:
            lines = "\n\n".join(f"{i+1}. {r}" for i, r in enumerate(reminders))
            msg = f"{title}\n\n{lines}"
            await member.send(msg)
            logger.info(f"📨 DM groupé envoyé à {user_id} ({len(reminders)} rappels)")
        except discord.Forbidden:
            logger.warning(f"Impossible d'envoyer un DM à {user_id} (DM fermés)")
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'envoi du DM groupé à {user_id}: {e}")
        await asyncio.sleep(1)


async def _reminder_already_sent(poll_id: int, reminder_type: str) -> bool:
    """Vérifie si un rappel a déjà été envoyé"""
    try:
        async with database.db.acquire() as conn:
            result = await conn.fetchrow(
                "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type=$2",
                poll_id, reminder_type
            )
        return result is not None
    except Exception as e:
        logger.error(f"❌ Erreur lors de la vérification du rappel: {e}")
        return True


async def _mark_reminder_sent(poll_id: int, reminder_type: str):
    """Marque un rappel comme envoyé"""
    try:
        async with database.db.acquire() as conn:
            await conn.execute(
                "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, $2)",
                poll_id, reminder_type
            )
    except Exception as e:
        logger.error(f"❌ Erreur lors du marquage du rappel: {e}")


async def send_non_voters_biweekly_reminders():
    """Envoie un rappel aux non-votants tous les 2 jours à 19h"""
    try:
        now = datetime.now(Config.TZ)
        
        async with database.db.acquire() as conn:
            polls = await conn.fetch("SELECT * FROM polls WHERE max_date IS NULL OR max_date > $1", now)
        
        user_reminders = defaultdict(list)

        for poll in polls:
            try:
                poll_age = now - poll["created_at"].replace(tzinfo=Config.TZ)
                days_since_creation = poll_age.days
                
                if days_since_creation > 0 and days_since_creation % 2 == 0:
                    if not await _reminder_already_sent(poll["id"], f"non_voters_day_{days_since_creation}"):
                        channel = database.bot.get_channel(poll["channel_id"])
                        if not channel:
                            continue
                        try:
                            message = await channel.fetch_message(poll["message_id"])
                        except (discord.NotFound, Exception):
                            continue

                        guild = channel.guild
                        votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll["id"])
                        voted_user_ids = {v["user_id"] for v in votes}
                        waiting_user_ids = {v["user_id"] for v in votes if v["emoji"] == "⏳"}

                        event_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
                        deadline_str = f" | ⏰ Limite : {poll['max_date'].strftime('%d/%m/%Y à %H:%M')}" if poll["max_date"] else ""
                        poll_summary = f"#{channel.name} — {poll['question']} | 📅 {event_str}{deadline_str} | 👉 {message.jump_url}"

                        for member in guild.members:
                            if member.bot:
                                continue
                            if not channel.permissions_for(member).read_messages:
                                continue
                            if poll["is_presence_poll"]:
                                if member.id not in voted_user_ids or member.id in waiting_user_ids:
                                    user_reminders[member.id].append(f"🔔 **N'oublie pas de voter !** — {poll_summary}")
                            else:
                                if member.id not in voted_user_ids:
                                    user_reminders[member.id].append(f"🔔 **N'oublie pas de voter !** — {poll_summary}")

                        await _mark_reminder_sent(poll["id"], f"non_voters_day_{days_since_creation}")
                        logger.info(f"✅ Rappel non-votants marqué pour le sondage {poll['id']} (jour {days_since_creation})")
                        
            except Exception as e:
                logger.error(f"❌ Erreur lors du rappel tous les 2 jours pour le sondage {poll['id']}: {e}")

        await _send_grouped_reminders(user_reminders, "🔔 **Rappels — Sondages en attente de vote**")
                
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi des rappels tous les 2 jours: {e}")


async def close_poll(poll):
    """Ferme un sondage et notifie les non-votants avec un DM groupé"""
    if await _reminder_already_sent(poll["id"], 'closed'):
        return

    channel = database.bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except discord.NotFound:
        logger.warning(f"Message {poll['message_id']} introuvable lors de la fermeture")
        return
    except Exception as e:
        logger.error(f"❌ Erreur lors de la récupération du message: {e}")
        return

    guild = channel.guild

    try:
        async with database.db.acquire() as conn:
            votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll["id"])

        voted_user_ids = set()
        waiting_user_ids = set()

        for v in votes:
            voted_user_ids.add(v["user_id"])
            if poll["is_presence_poll"] and v["emoji"] == "⏳":
                waiting_user_ids.add(v["user_id"])

        user_reminders = defaultdict(list)

        for member in guild.members:
            if member.bot:
                continue
            if not channel.permissions_for(member).read_messages:
                continue

            if poll["is_presence_poll"]:
                if member.id not in voted_user_ids or member.id in waiting_user_ids:
                    status = "⏳ En attente non confirmée" if member.id in waiting_user_ids else "❌ Pas voté"
                    user_reminders[member.id].append(f"🔒 **Le vote est terminé !** — #{channel.name} — {poll['question']} | 📅 {poll['event_date'].strftime('%d/%m/%Y à %H:%M')} | {status} | 👉 {message.jump_url}")
            else:
                if member.id not in voted_user_ids:
                    user_reminders[member.id].append(f"🔒 **Le vote est terminé !** — #{channel.name} — {poll['question']} | 📅 {poll['event_date'].strftime('%d/%m/%Y à %H:%M')} | ❌ Pas voté | 👉 {message.jump_url}")

        if user_reminders:
            await message.edit(view=None)
            await update_poll_display(message, poll["id"])
            total_notified = sum(len(v) for v in user_reminders.values())
            await _send_grouped_reminders(user_reminders, "🔒 **Résultats — Vote terminé**")
            logger.info(f"🔒 Sondage {poll['id']} fermé, {total_notified} notifications envoyées")

        await _mark_reminder_sent(poll['id'], 'closed')
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la fermeture du sondage {poll['id']}: {e}")