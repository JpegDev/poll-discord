import discord
import asyncpg
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from utils.config import Config
from utils import database

logger = logging.getLogger(__name__)


async def create_poll(interaction: discord.Interaction, question: str, options: list, 
                     is_presence: bool, event_date: datetime, max_date: datetime = None,
                     allow_multiple: bool = False):
    """Crée un sondage en base et envoie le message"""
    try:
        from utils.views import PollView, PresencePollView
        
        if is_presence:
            view = PresencePollView(0)
        else:
            view = PollView(0, options, allow_multiple)

        await interaction.response.send_message(content="📊 _Chargement..._", view=view)
        message = await interaction.original_response()

        async with database.db.acquire() as conn:
            poll_id = await conn.fetchval("""
                INSERT INTO polls (message_id, channel_id, question, options, is_presence_poll, 
                                  event_date, max_date, allow_multiple)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            """, message.id, interaction.channel_id, question, options, is_presence, 
               event_date, max_date, allow_multiple)

        logger.info(f"✅ Sondage créé: id={poll_id}, question='{question[:50]}', multiple={allow_multiple}")

        if is_presence:
            view = PresencePollView(poll_id)
        else:
            view = PollView(poll_id, options, allow_multiple)

        await update_poll_display(message, poll_id)
        await message.edit(view=view)
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la création du sondage: {e}")
        await interaction.followup.send("❌ Erreur lors de la création du sondage", ephemeral=True)


async def update_poll_display(message: discord.Message, poll_id: int):
    """Met à jour l'affichage d'un sondage"""
    try:
        async with database.db.acquire() as conn:
            poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", poll_id)
            if not poll:
                logger.warning(f"Sondage {poll_id} introuvable")
                return

            votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll_id)

        vote_counts = defaultdict(list)
        user_votes = defaultdict(list)
        
        for vote in votes:
            vote_counts[vote["emoji"]].append(vote["user_id"])
            user_votes[vote["user_id"]].append(vote["emoji"])

        content = _build_poll_content(poll, vote_counts, user_votes, message.guild, message.channel, votes)

        if len(content) > Config.MAX_CONTENT_LENGTH:
            content = content[:Config.MAX_CONTENT_LENGTH - 3] + "..."

        await message.edit(content=content)
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la mise à jour du sondage {poll_id}: {e}")


def _build_poll_content(poll, vote_counts, user_votes, guild, channel, votes) -> str:
    """Construit le contenu textuel d'un sondage"""
    mode_text = ""
    if not poll["is_presence_poll"]:
        mode_text = " 🔘 Choix unique" if not poll["allow_multiple"] else " ☑️ Choix multiple"
    
    content_parts = [f"# 📊 {poll['question']}{mode_text}\n"]

    if poll["is_presence_poll"]:
        _add_presence_votes(content_parts, vote_counts)
    else:
        _add_option_votes(content_parts, poll["options"], vote_counts, poll["allow_multiple"])

    content_parts.append("")

    _add_non_voters_section(content_parts, poll, guild, channel, votes, vote_counts)
    _add_dates_section(content_parts, poll)

    return "\n".join(content_parts)


def _add_presence_votes(content_parts, vote_counts):
    """Ajoute les votes de présence au contenu"""
    for emoji, label in [("✅", "Présent"), ("⏳", "En attente"), ("❌", "Absent")]:
        users = vote_counts.get(emoji, [])
        count = len(users)
        mentions = ", ".join([f"<@{uid}>" for uid in users[:Config.MAX_MENTIONS_DISPLAY]])
        if len(users) > Config.MAX_MENTIONS_DISPLAY:
            mentions += f" _et {len(users) - Config.MAX_MENTIONS_DISPLAY} autres..._"
        
        content_parts.append(f"**{emoji} {label} ({count})**")
        content_parts.append(f"{mentions if mentions else '_Aucun_'}\n")


def _add_option_votes(content_parts, options, vote_counts, allow_multiple):
    """Ajoute les votes d'options au contenu"""
    for i, option in enumerate(options):
        emoji = Config.EMOJIS[i]
        users = vote_counts.get(emoji, [])
        count = len(users)
        mentions = ", ".join([f"<@{uid}>" for uid in users[:Config.MAX_MENTIONS_DISPLAY]])
        if len(users) > Config.MAX_MENTIONS_DISPLAY:
            mentions += f" _et {len(users) - Config.MAX_MENTIONS_DISPLAY} autres..._"
        
        content_parts.append(f"**{emoji} {option} ({count})**")
        content_parts.append(f"{mentions if mentions else '_Aucun_'}\n")


def _add_non_voters_section(content_parts, poll, guild, channel, votes, vote_counts):
    """Ajoute la section des non-votants et personnes en attente"""
    if not guild:
        return
        
    all_members = [m for m in guild.members if not m.bot and (not channel or channel.permissions_for(m).read_messages)]
    voted_user_ids = set(v["user_id"] for v in votes)

    if poll["is_presence_poll"]:
        waiting_user_ids = set(v["user_id"] for v in votes if v["emoji"] == "⏳")
        non_voted = [m for m in all_members if m.id not in voted_user_ids]

        if non_voted:
            mentions = ", ".join([m.mention for m in non_voted[:Config.MAX_MENTIONS_DISPLAY]])
            if len(non_voted) > Config.MAX_MENTIONS_DISPLAY:
                mentions += f" _et {len(non_voted) - Config.MAX_MENTIONS_DISPLAY} autres..._"
            content_parts.append(f"**❓ Non-votants ({len(non_voted)})**\n{mentions}\n")

        if waiting_user_ids:
            waiting_members = [guild.get_member(uid) for uid in waiting_user_ids if guild.get_member(uid)]
            mentions = ", ".join([m.mention for m in waiting_members[:Config.MAX_MENTIONS_DISPLAY]])
            if len(waiting_members) > Config.MAX_MENTIONS_DISPLAY:
                mentions += f" _et {len(waiting_members) - Config.MAX_MENTIONS_DISPLAY} autres..._"
            content_parts.append(f"**⏳ En attente de confirmation ({len(waiting_members)})**\n{mentions}\n")
    else:
        non_voted = [m for m in all_members if m.id not in voted_user_ids]
        if non_voted:
            mentions = ", ".join([m.mention for m in non_voted[:Config.MAX_MENTIONS_DISPLAY]])
            if len(non_voted) > Config.MAX_MENTIONS_DISPLAY:
                mentions += f" _et {len(non_voted) - Config.MAX_MENTIONS_DISPLAY} autres..._"
            content_parts.append(f"**❓ Non-votants ({len(non_voted)})**\n{mentions}\n")


def _add_dates_section(content_parts, poll):
    """Ajoute la section des dates au contenu"""
    content_parts.append("")
    event_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
    content_parts.append(f"**📅 Événement :** {event_str}")

    if poll["max_date"]:
        max_str = poll["max_date"].strftime("%d/%m/%Y à %H:%M")
        content_parts.append(f"**⏰ Date limite de vote :** {max_str}")

    now = datetime.now(Config.TZ)
    if poll["max_date"] and now > poll["max_date"]:
        content_parts.append("\n🔒 **Le vote est terminé**")