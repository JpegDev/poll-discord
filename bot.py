import os
import asyncio
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

logging.basicConfig(level=logging.INFO)

# -------------------- Intents --------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -------------------- Database --------------------
DATABASE_URL = os.getenv("DATABASE_URL")
db = None
TZ_FR = ZoneInfo("Europe/Paris")

async def get_db():
    return await asyncpg.create_pool(DATABASE_URL)

async def init_db():
    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                id SERIAL PRIMARY KEY,
                message_id BIGINT UNIQUE,
                channel_id BIGINT,
                question TEXT,
                options TEXT[],
                event_date TIMESTAMP WITH TIME ZONE NOT NULL,
                max_date TIMESTAMP WITH TIME ZONE,
                is_presence_poll BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                poll_id INTEGER REFERENCES polls(id) ON DELETE CASCADE,
                user_id BIGINT,
                emoji TEXT,
                PRIMARY KEY (poll_id, user_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders_sent (
                poll_id INTEGER REFERENCES polls(id) ON DELETE CASCADE,
                sent_at TIMESTAMP DEFAULT NOW(),
                reminder_type TEXT
            );
        """)
        logging.info("✅ Tables vérifiées.")

# -------------------- Fonctions de parsing de dates --------------------
def parse_date(date_str: str) -> datetime:
    """
    Parse une date au format JJ/MM/AAAA ou JJ/MM/AAAA-HH:mm
    Retourne un datetime en timezone Paris
    """
    date_str = date_str.strip()
    
    # Format avec heure
    match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})-(\d{1,2}):(\d{2})', date_str)
    if match:
        day, month, year, hour, minute = map(int, match.groups())
        return datetime(year, month, day, hour, minute, tzinfo=TZ_FR)
    
    # Format sans heure (on prend 23:59)
    match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_str)
    if match:
        day, month, year = map(int, match.groups())
        return datetime(year, month, day, 23, 59, tzinfo=TZ_FR)
    
    raise ValueError("Format invalide. Utilisez JJ/MM/AAAA ou JJ/MM/AAAA-HH:mm")

# -------------------- Modal pour saisie des dates --------------------
class DateModal(Modal, title="📅 Configuration du sondage"):
    event_date_input = TextInput(
        label="Date de l'événement (obligatoire)",
        placeholder="JJ/MM/AAAA ou JJ/MM/AAAA-HH:mm (ex: 25/12/2024-20:00)",
        required=True,
        max_length=16
    )
    
    max_date_input = TextInput(
        label="Date limite de vote (optionnel)",
        placeholder="JJ/MM/AAAA ou JJ/MM/AAAA-HH:mm",
        required=False,
        max_length=16
    )
    
    def __init__(self, question: str, options: list, is_presence: bool):
        super().__init__()
        self.question = question
        self.options = options
        self.is_presence = is_presence
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            event_date = parse_date(self.event_date_input.value)
            
            max_date = None
            if self.max_date_input.value:
                max_date = parse_date(self.max_date_input.value)
                
                # Vérifications
                if max_date >= event_date:
                    await interaction.response.send_message(
                        "❌ La date limite de vote doit être avant la date de l'événement.",
                        ephemeral=True
                    )
                    return
            
            # Vérifier que les dates ne sont pas dans le passé
            now = datetime.now(TZ_FR)
            if event_date <= now:
                await interaction.response.send_message(
                    "❌ La date de l'événement doit être dans le futur.",
                    ephemeral=True
                )
                return
            
            if max_date and max_date <= now:
                await interaction.response.send_message(
                    "❌ La date limite de vote doit être dans le futur.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer()
            
            # Créer le sondage
            await create_poll_message(
                interaction,
                self.question,
                self.options,
                event_date,
                max_date,
                self.is_presence
            )
            
        except ValueError as e:
            await interaction.response.send_message(f"❌ {str(e)}", ephemeral=True)
        except Exception as e:
            logging.error(f"Erreur dans DateModal: {e}")
            await interaction.response.send_message(
                "❌ Une erreur est survenue lors de la création du sondage.",
                ephemeral=True
            )

# -------------------- Classes pour les boutons --------------------
class PollButton(Button):
    def __init__(self, label, emoji, poll_id, db, is_presence=False):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary if not is_presence else (
                discord.ButtonStyle.success if emoji == "✅" else
                discord.ButtonStyle.secondary if emoji == "⏳" else
                discord.ButtonStyle.danger
            ),
            emoji=emoji,
            custom_id=f"poll_{poll_id}_{emoji}"
        )
        self.poll_id = poll_id
        self.db = db
        self.is_presence = is_presence

    async def callback(self, interaction: discord.Interaction):
        # Vérifier que le vote est toujours possible
        async with self.db.acquire() as conn:
            poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", self.poll_id)
            
        if not poll:
            await interaction.response.send_message("❌ Sondage introuvable.", ephemeral=True)
            return
        
        now = datetime.now(TZ_FR)
        max_date = poll["max_date"]
        
        if max_date and now > max_date:
            await interaction.response.send_message(
                "❌ Le vote est terminé, la date limite est dépassée.",
                ephemeral=True
            )
            return
        
        user_id = interaction.user.id
        emoji_str = str(self.emoji)

        async with self.db.acquire() as conn:
            existing_vote = await conn.fetchrow(
                "SELECT emoji FROM votes WHERE poll_id=$1 AND user_id=$2",
                self.poll_id, user_id
            )

            if existing_vote:
                if existing_vote["emoji"] == emoji_str:
                    # Retirer le vote
                    await conn.execute(
                        "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                        self.poll_id, user_id
                    )
                else:
                    # Changer le vote
                    await conn.execute(
                        "UPDATE votes SET emoji=$1 WHERE poll_id=$2 AND user_id=$3",
                        emoji_str, self.poll_id, user_id
                    )
            else:
                # Nouveau vote
                await conn.execute(
                    "INSERT INTO votes (poll_id, user_id, emoji) VALUES ($1, $2, $3)",
                    self.poll_id, user_id, emoji_str
                )

        # Mettre à jour l'affichage
        await update_poll_display(interaction.message, self.poll_id)
        await interaction.response.defer()

class PollView(View):
    def __init__(self, poll_id, options, db, is_presence=False):
        super().__init__(timeout=None)
        
        if is_presence:
            # Bot de présence avec 3 boutons fixes
            self.add_item(PollButton("Présent", "✅", poll_id, db, True))
            self.add_item(PollButton("En attente", "⏳", poll_id, db, True))
            self.add_item(PollButton("Absent", "❌", poll_id, db, True))
        else:
            # Bot classique avec choix personnalisés
            for i, opt in enumerate(options):
                emoji = chr(0x1F1E6 + i)
                self.add_item(PollButton(opt, emoji, poll_id, db, False))

# -------------------- Mise à jour de l'affichage --------------------
async def update_poll_display(message: discord.Message, poll_id: int):
    """Met à jour l'affichage d'un sondage"""
    async with db.acquire() as conn:
        poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", poll_id)
        votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll_id)

    if not poll:
        return

    voted_user_ids = set()
    results = {}
    for v in votes:
        results.setdefault(v["emoji"], []).append(v["user_id"])
        voted_user_ids.add(v["user_id"])

    channel = message.channel
    guild = channel.guild
    non_voters = [
        m for m in guild.members
        if not m.bot and m.id not in voted_user_ids
        and channel.permissions_for(m).read_messages
    ]
    
    # Filtrer les "en attente" pour le bot de présence
    waiting_users = []
    if poll["is_presence_poll"]:
        waiting_users = results.get("⏳", [])
        non_voters_and_waiting = [m for m in guild.members
                                   if not m.bot
                                   and (m.id not in voted_user_ids or m.id in waiting_users)
                                   and channel.permissions_for(m).read_messages]
    else:
        non_voters_and_waiting = non_voters

    # Construction du message
    if poll["is_presence_poll"]:
        # Affichage pour bot de présence
        lines = [f"📊 **{poll['question']}**\n"]
        
        emojis_map = {"✅": "Présent", "⏳": "En attente", "❌": "Absent"}
        for emoji, label in emojis_map.items():
            voters = results.get(emoji, [])
            if voters:
                mentions = ", ".join(f"<@{uid}>" for uid in voters)
                lines.append(f"{emoji} **{label}** ({len(voters)}) : {mentions}\n")
            else:
                lines.append(f"{emoji} **{label}** (0)\n")
    else:
        # Affichage pour bot classique
        emojis = [chr(0x1F1E6 + i) for i in range(len(poll["options"]))]
        lines = [f"📊 **{poll['question']}**\n"]
        for i, opt in enumerate(poll["options"]):
            emoji = emojis[i]
            voters = results.get(emoji, [])
            if voters:
                mentions = ", ".join(f"<@{uid}>" for uid in voters)
                lines.append(f"{emoji} **{opt}** ({len(voters)}) : {mentions}\n")
            else:
                lines.append(f"{emoji} **{opt}** (0)\n")

    # Non-votants
    if non_voters_and_waiting:
        mentions = ", ".join(f"<@{m.id}>" for m in non_voters_and_waiting)
        lines.append(f"\n👥 **Non-votants / En attente ({len(non_voters_and_waiting)})** : {mentions}\n")
    else:
        lines.append("\n👥 **Non-votants / En attente** : 0\n")
    
    # Informations sur les dates
    event_date = poll["event_date"]
    max_date = poll["max_date"]
    
    lines.append(f"\n📅 **Événement** : {event_date.strftime('%d/%m/%Y à %H:%M')}")
    if max_date:
        lines.append(f"⏰ **Date limite de vote** : {max_date.strftime('%d/%m/%Y à %H:%M')}")
    
    # Vérifier si le vote est terminé
    now = datetime.now(TZ_FR)
    view = PollView(poll["id"], poll["options"], db, poll["is_presence_poll"])
    
    if max_date and now > max_date:
        lines.append("\n🔒 **Le vote est terminé**")
        view = None  # Retirer les boutons

    await message.edit(
        content="\n".join(lines) + "\n\u200b",
        embeds=[],
        view=view,
        allowed_mentions=discord.AllowedMentions(users=True)
    )

# -------------------- Création du message de sondage --------------------
async def create_poll_message(interaction: discord.Interaction, question: str,
                              options: list, event_date: datetime,
                              max_date: datetime | None, is_presence: bool):
    """Crée le message du sondage dans le canal"""
    
    # Construire l'embed initial
    if is_presence:
        description = f"**{question}**\n\n✅ Présent\n⏳ En attente\n❌ Absent"
    else:
        description = f"**{question}**\n\n" + "\n".join(
            f"{chr(0x1F1E6+i)} {opt}" for i, opt in enumerate(options)
        )
    
    description += f"\n\n📅 **Événement** : {event_date.strftime('%d/%m/%Y à %H:%M')}"
    if max_date:
        description += f"\n⏰ **Date limite** : {max_date.strftime('%d/%m/%Y à %H:%M')}"

    embed = discord.Embed(
        title="📊 Nouveau sondage",
        description=description,
        color=discord.Color.blurple() if not is_presence else discord.Color.green()
    )

    message = await interaction.channel.send(embed=embed)

    # Enregistrer dans la base de données
    async with db.acquire() as conn:
        poll_id = (await conn.fetchrow(
            """INSERT INTO polls (message_id, channel_id, question, options, event_date, max_date, is_presence_poll) 
               VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
            message.id, interaction.channel.id, question, options, event_date, max_date, is_presence
        ))["id"]

    # Ajouter les boutons
    view = PollView(poll_id, options, db, is_presence)
    await message.edit(embed=None, content=embed.description, view=view)

    await interaction.followup.send(f"✅ Sondage créé → {message.jump_url}", ephemeral=True)

# -------------------- Bot Ready --------------------
@bot.event
async def on_ready():
    global db
    db = await get_db()
    await init_db()
    await tree.sync()
    logging.info(f"🟢 Connecté en tant que {bot.user}")

    # Restaurer les vues des sondages existants
    async with db.acquire() as conn:
        polls = await conn.fetch("SELECT * FROM polls")
        for poll in polls:
            channel = bot.get_channel(poll["channel_id"])
            if not channel:
                continue
            try:
                message = await channel.fetch_message(poll["message_id"])
            except discord.NotFound:
                continue

            # Ne pas ajouter de vue si la date limite est dépassée
            now = datetime.now(TZ_FR)
            if poll["max_date"] and now > poll["max_date"]:
                continue

            bot.add_view(
                PollView(poll["id"], poll["options"], db, poll["is_presence_poll"]),
                message_id=poll["message_id"]
            )

    # Lancer le scheduler des rappels
    bot.loop.create_task(reminder_scheduler())
    logging.info("⏳ Scheduler des rappels lancé")

# -------------------- Slash Command /poll --------------------
@tree.command(name="poll", description="Créer un sondage personnalisé ou de présence")
async def poll(interaction: discord.Interaction,
               question: str = "Dispo ?",
               choix1: str | None = None,
               choix2: str | None = None,
               choix3: str | None = None,
               choix4: str | None = None,
               choix5: str | None = None,
               choix6: str | None = None,
               choix7: str | None = None,
               choix8: str | None = None,
               choix9: str | None = None,
               choix10: str | None = None,
               choix11: str | None = None,
               choix12: str | None = None,
               choix13: str | None = None,
               choix14: str | None = None,
               choix15: str | None = None,
               choix16: str | None = None,
               choix17: str | None = None,
               choix18: str | None = None,
               choix19: str | None = None,
               choix20: str | None = None):

    options = [c for c in [
        choix1, choix2, choix3, choix4, choix5, choix6, choix7, choix8, choix9, choix10,
        choix11, choix12, choix13, choix14, choix15, choix16, choix17, choix18, choix19, choix20
    ] if c]

    # Déterminer le type de sondage
    is_presence = len(options) == 0
    
    if not is_presence and len(options) < 2:
        return await interaction.response.send_message(
            "❌ Il faut soit laisser tous les choix vides (bot de présence), soit remplir au moins 2 choix.",
            ephemeral=True
        )

    # Ouvrir le modal pour les dates
    modal = DateModal(question, options, is_presence)
    await interaction.response.send_modal(modal)

# -------------------- Système de rappels --------------------
async def send_reminders():
    """Envoie les rappels aux utilisateurs n'ayant pas voté"""
    logging.info("📬 Vérification des rappels à envoyer...")
    
    now = datetime.now(TZ_FR)
    
    async with db.acquire() as conn:
        polls = await conn.fetch("""
            SELECT * FROM polls 
            WHERE event_date > $1
        """, now)
        
        for poll in polls:
            # Vérifier si l'événement est passé
            if poll["event_date"] <= now:
                continue
            
            # Vérifier si le vote est terminé
            if poll["max_date"] and poll["max_date"] <= now:
                # Envoyer message aux non-votants que c'est terminé
                await send_vote_closed_messages(poll)
                continue
            
            # Déterminer si on doit envoyer un rappel
            should_send = False
            reminder_type = ""
            
            if poll["max_date"]:
                # Avec date butoir : rappels J-2 et J-1
                days_until_deadline = (poll["max_date"] - now).days
                
                if days_until_deadline == 1:
                    # Vérifier si rappel J-1 déjà envoyé
                    last_reminder = await conn.fetchrow(
                        "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type='J-1'",
                        poll["id"]
                    )
                    if not last_reminder:
                        should_send = True
                        reminder_type = "J-1"
                        
                elif days_until_deadline == 2:
                    # Vérifier si rappel J-2 déjà envoyé
                    last_reminder = await conn.fetchrow(
                        "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type='J-2'",
                        poll["id"]
                    )
                    if not last_reminder:
                        should_send = True
                        reminder_type = "J-2"
            else:
                # Sans date butoir : rappel hebdomadaire
                last_reminder = await conn.fetchrow(
                    "SELECT * FROM reminders_sent WHERE poll_id=$1 ORDER BY sent_at DESC LIMIT 1",
                    poll["id"]
                )
                
                if not last_reminder:
                    # Premier rappel (24h après création)
                    if (now - poll["created_at"]).days >= 1:
                        should_send = True
                        reminder_type = "weekly"
                else:
                    # Rappel hebdomadaire
                    if (now - last_reminder["sent_at"]).days >= 7:
                        should_send = True
                        reminder_type = "weekly"
            
            if should_send:
                await send_poll_reminders(poll, reminder_type)
                await conn.execute(
                    "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, $2)",
                    poll["id"], reminder_type
                )

async def send_poll_reminders(poll, reminder_type: str):
    """Envoie les rappels pour un sondage spécifique"""
    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(poll["message_id"])
    except discord.NotFound:
        return
    
    guild = channel.guild
    
    async with db.acquire() as conn:
        votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll["id"])
    
    voted_user_ids = set()
    waiting_user_ids = set()
    
    for v in votes:
        voted_user_ids.add(v["user_id"])
        if poll["is_presence_poll"] and v["emoji"] == "⏳":
            waiting_user_ids.add(v["user_id"])
    
    # Liste des personnes à rappeler
    to_remind = []
    for member in guild.members:
        if member.bot:
            continue
        if not channel.permissions_for(member).read_messages:
            continue
        
        # Pour bot de présence : rappeler non-votants ET en attente
        if poll["is_presence_poll"]:
            if member.id not in voted_user_ids or member.id in waiting_user_ids:
                to_remind.append(member)
        else:
            # Pour bot classique : rappeler seulement non-votants
            if member.id not in voted_user_ids:
                to_remind.append(member)
    
    if not to_remind:
        return
    
    # Construire le message
    event_date_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
    
    if reminder_type == "J-1":
        subject = f"⚠️ **Dernier jour** pour voter !"
        deadline_info = f"Date limite : **demain** ({poll['max_date'].strftime('%d/%m/%Y à %H:%M')})"
    elif reminder_type == "J-2":
        subject = f"⏰ **2 jours** avant la date limite !"
        deadline_info = f"Date limite : {poll['max_date'].strftime('%d/%m/%Y à %H:%M')}"
    else:
        subject = "📢 Rappel de vote"
        deadline_info = ""
    
    # Envoi des MP
    for member in to_remind:
        try:
            msg_parts = [
                subject,
                f"\n**{poll['question']}**",
                f"📅 Événement : {event_date_str}"
            ]
            
            if deadline_info:
                msg_parts.append(deadline_info)
            
            if poll["is_presence_poll"] and member.id in waiting_user_ids:
                msg_parts.append("\n⏳ Tu es actuellement en **attente**, pense à confirmer ta présence !")
            else:
                msg_parts.append("\n❓ Tu n'as pas encore voté !")
            
            msg_parts.append(f"\n👉 {message.jump_url}")
            
            await member.send("\n".join(msg_parts))
        except discord.Forbidden:
            logging.warning(f"Impossible d'envoyer un MP à {member.name}")
        except Exception as e:
            logging.error(f"Erreur lors de l'envoi du rappel à {member.name}: {e}")

async def send_vote_closed_messages(poll):
    """Envoie un message aux non-votants/en attente quand le vote est terminé"""
    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(poll["message_id"])
    except discord.NotFound:
        return
    
    guild = channel.guild
    
    async with db.acquire() as conn:
        votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll["id"])
        
        # Vérifier si message déjà envoyé
        already_sent = await conn.fetchrow(
            "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type='closed'",
            poll["id"]
        )
        
        if already_sent:
            return
    
    voted_user_ids = set()
    waiting_user_ids = set()
    
    for v in votes:
        voted_user_ids.add(v["user_id"])
        if poll["is_presence_poll"] and v["emoji"] == "⏳":
            waiting_user_ids.add(v["user_id"])
    
    to_notify = []
    for member in guild.members:
        if member.bot:
            continue
        if not channel.permissions_for(member).read_messages:
            continue
        
        if poll["is_presence_poll"]:
            if member.id not in voted_user_ids or member.id in waiting_user_ids:
                to_notify.append(member)
        else:
            if member.id not in voted_user_ids:
                to_notify.append(member)
    
    if not to_notify:
        return
    
    # Retirer les boutons du message
    await message.edit(view=None)
    await update_poll_display(message, poll["id"])
    
    # Envoyer les messages
    event_date_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
    
    for member in to_notify:
        try:
            msg = f"🔒 **Le vote est terminé !**\n\n**{poll['question']}**\n📅 Événement : {event_date_str}\n"
            
            if poll["is_presence_poll"] and member.id in waiting_user_ids:
                msg += "\n⏳ Tu étais en attente et n'as pas confirmé ta présence."
            else:
                msg += "\n❌ Tu n'as pas voté à temps."
            
            msg += f"\n\n👉 {message.jump_url}"
            
            await member.send(msg)
        except:
            pass
    
    # Marquer comme envoyé
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, 'closed')",
            poll["id"]
        )

async def reminder_scheduler():
    """Scheduler qui vérifie régulièrement s'il faut envoyer des rappels"""
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        try:
            await send_reminders()
        except Exception as e:
            logging.error(f"Erreur dans le scheduler de rappels: {e}")
        
        # Vérifier toutes les heures
        await asyncio.sleep(3600)

# -------------------- Run Bot --------------------
bot.run(os.getenv("TOKEN_DISCORD"))
