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
        await conn.execute("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                              WHERE table_name='polls' AND column_name='event_date') THEN
                    ALTER TABLE polls ADD COLUMN event_date TIMESTAMP WITH TIME ZONE;
                END IF;

                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                              WHERE table_name='polls' AND column_name='max_date') THEN
                    ALTER TABLE polls ADD COLUMN max_date TIMESTAMP WITH TIME ZONE;
                END IF;

                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                              WHERE table_name='polls' AND column_name='is_presence_poll') THEN
                    ALTER TABLE polls ADD COLUMN is_presence_poll BOOLEAN DEFAULT FALSE;
                END IF;
            END $$;
        """)

# -------------------- Views --------------------
class PollView(View):
    def __init__(self, poll_id: int, options: list):
        super().__init__(timeout=None)
        self.poll_id = poll_id

        emojis = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮", "🇯",
                  "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷", "🇸", "🇹"]

        for i, option in enumerate(options[:20]):
            button = Button(
                label=option,
                emoji=emojis[i],
                style=discord.ButtonStyle.primary,
                custom_id=f"poll_{poll_id}_{emojis[i]}"
            )
            button.callback = self.make_callback(emojis[i])
            self.add_item(button)

    def make_callback(self, emoji):
        async def callback(interaction: discord.Interaction):
            async with db.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT emoji FROM votes WHERE poll_id=$1 AND user_id=$2",
                    self.poll_id, interaction.user.id
                )

                if existing and existing["emoji"] == emoji:
                    await conn.execute(
                        "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                        self.poll_id, interaction.user.id
                    )
                    # await interaction.response.send_message("✅ Vote annulé", ephemeral=True)
                else:
                    await conn.execute("""
                        INSERT INTO votes (poll_id, user_id, emoji) 
                        VALUES ($1, $2, $3)
                        ON CONFLICT (poll_id, user_id) 
                        DO UPDATE SET emoji=$3
                    """, self.poll_id, interaction.user.id, emoji)
                    # await interaction.response.send_message("✅ Vote enregistré", ephemeral=True)

            await update_poll_display(interaction.message, self.poll_id)

        return callback

class PresencePollView(View):
    def __init__(self, poll_id: int):
        super().__init__(timeout=None)
        self.poll_id = poll_id

        # Bouton Présent
        btn_yes = Button(label="Présent", emoji="✅", style=discord.ButtonStyle.success, custom_id=f"presence_{poll_id}_yes")
        btn_yes.callback = self.make_callback("✅")
        self.add_item(btn_yes)

        # Bouton En attente
        btn_maybe = Button(label="En attente", emoji="⏳", style=discord.ButtonStyle.secondary, custom_id=f"presence_{poll_id}_maybe")
        btn_maybe.callback = self.make_callback("⏳")
        self.add_item(btn_maybe)

        # Bouton Absent
        btn_no = Button(label="Absent", emoji="❌", style=discord.ButtonStyle.danger, custom_id=f"presence_{poll_id}_no")
        btn_no.callback = self.make_callback("❌")
        self.add_item(btn_no)

    def make_callback(self, emoji):
        async def callback(interaction: discord.Interaction):
            async with db.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT emoji FROM votes WHERE poll_id=$1 AND user_id=$2",
                    self.poll_id, interaction.user.id
                )

                if existing and existing["emoji"] == emoji:
                    await conn.execute(
                        "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                        self.poll_id, interaction.user.id
                    )
                    # await interaction.response.send_message("✅ Vote annulé", ephemeral=True)
                else:
                    await conn.execute("""
                        INSERT INTO votes (poll_id, user_id, emoji) 
                        VALUES ($1, $2, $3)
                        ON CONFLICT (poll_id, user_id) 
                        DO UPDATE SET emoji=$3
                    """, self.poll_id, interaction.user.id, emoji)
                    # await interaction.response.send_message("✅ Vote enregistré", ephemeral=True)

            await update_poll_display(interaction.message, self.poll_id)

        return callback

# -------------------- Modal --------------------
class DateModal(Modal, title="📅 Dates de l'événement"):
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

    def __init__(self, question: str, options: list, is_presence: bool):
        super().__init__()
        self.question = question
        self.options = options
        self.is_presence = is_presence

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parser event_date
            event_str = self.event_date.value.strip()
            if "-" in event_str:
                event_dt = datetime.strptime(event_str, "%d/%m/%Y-%H:%M").replace(tzinfo=TZ_FR)
            else:
                event_dt = datetime.strptime(event_str, "%d/%m/%Y").replace(hour=0, minute=0, tzinfo=TZ_FR)

            # Parser max_date
            max_dt = None
            if self.max_date.value.strip():
                max_str = self.max_date.value.strip()
                if "-" in max_str:
                    max_dt = datetime.strptime(max_str, "%d/%m/%Y-%H:%M").replace(tzinfo=TZ_FR)
                else:
                    max_dt = datetime.strptime(max_str, "%d/%m/%Y").replace(hour=23, minute=59, tzinfo=TZ_FR)

            # Validations
            now = datetime.now(TZ_FR)
            if event_dt < now:
                await interaction.response.send_message("❌ La date de l'événement ne peut pas être dans le passé", ephemeral=True)
                return

            if max_dt:
                if max_dt < now:
                    await interaction.response.send_message("❌ La date limite ne peut pas être dans le passé", ephemeral=True)
                    return
                if max_dt > event_dt:
                    await interaction.response.send_message("❌ La date limite doit être avant la date de l'événement", ephemeral=True)
                    return

            # Créer le sondage
            await create_poll(interaction, self.question, self.options, self.is_presence, event_dt, max_dt)

        except ValueError:
            await interaction.response.send_message("❌ Format de date invalide. Utilisez JJ/MM/AAAA ou JJ/MM/AAAA-HH:mm", ephemeral=True)

# -------------------- Functions --------------------
async def create_poll(interaction: discord.Interaction, question: str, options: list, is_presence: bool, event_date: datetime, max_date: datetime = None):
    """Crée un sondage en base et envoie le message"""

    # Créer le message embed initial
    if is_presence:
        embed = discord.Embed(title=f"📊 {question}", color=discord.Color.green())
        view = PresencePollView(0)  # ID temporaire
    else:
        embed = discord.Embed(title=f"📊 {question}", color=discord.Color.blue())
        view = PollView(0, options)  # ID temporaire

    embed.description = "_Chargement..._"

    # 🆕 ENVOYER UNIQUEMENT L'EMBED (pas de content)
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    # Enregistrer en base
    async with db.acquire() as conn:
        poll_id = await conn.fetchval("""
            INSERT INTO polls (message_id, channel_id, question, options, is_presence_poll, event_date, max_date)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        """, message.id, interaction.channel_id, question, options, is_presence, event_date, max_date)

    # Recréer la vue avec le bon ID
    if is_presence:
        view = PresencePollView(poll_id)
    else:
        view = PollView(poll_id, options)

    await update_poll_display(message, poll_id)
    await message.edit(view=view)

async def update_poll_display(message: discord.Message, poll_id: int):
    """Met à jour l'affichage d'un sondage"""
    async with db.acquire() as conn:
        poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", poll_id)
        if not poll:
            return

        votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll_id)

    # Organiser les votes
    vote_counts = defaultdict(list)
    for vote in votes:
        vote_counts[vote["emoji"]].append(vote["user_id"])

    # Construire l'embed
    if poll["is_presence_poll"]:
        embed = discord.Embed(title=f"📊 {poll['question']}", color=discord.Color.green())

        for emoji, label in [("✅", "Présent"), ("⏳", "En attente"), ("❌", "Absent")]:
            users = vote_counts.get(emoji, [])
            if users:
                mentions = ", ".join([f"<@{uid}>" for uid in users])
                embed.add_field(name=f"{emoji} {label} ({len(users)})", value=mentions, inline=False)
            else:
                embed.add_field(name=f"{emoji} {label} (0)", value="_Aucun_", inline=False)
    else:
        embed = discord.Embed(title=f"📊 {poll['question']}", color=discord.Color.blue())

        emojis = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮", "🇯",
                  "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷", "🇸", "🇹"]

        for i, option in enumerate(poll["options"]):
            emoji = emojis[i]
            users = vote_counts.get(emoji, [])
            if users:
                mentions = ", ".join([f"<@{uid}>" for uid in users])
                embed.add_field(name=f"{emoji} {option} ({len(users)})", value=mentions, inline=False)
            else:
                embed.add_field(name=f"{emoji} {option} (0)", value="_Aucun_", inline=False)

    # 🆕 SÉPARATION Non-votants / En attente de confirmation
    guild = message.guild
    channel = message.channel

    all_members = [m for m in guild.members if not m.bot and channel.permissions_for(m).read_messages]
    voted_user_ids = set(v["user_id"] for v in votes)

    if poll["is_presence_poll"]:
        # Ceux qui ont voté "En attente"
        waiting_user_ids = set(v["user_id"] for v in votes if v["emoji"] == "⏳")
        
        # Ceux qui n'ont PAS voté du tout
        non_voted = [m for m in all_members if m.id not in voted_user_ids]
        
        # Afficher les non-votants
        if non_voted:
            mentions = ", ".join([m.mention for m in non_voted[:20]])
            if len(non_voted) > 20:
                mentions += f" _et {len(non_voted) - 20} autres..._"
            embed.add_field(name=f"❓ Non-votants ({len(non_voted)})", value=mentions, inline=False)

        # Afficher séparément ceux en attente de confirmation
        if waiting_user_ids:
            waiting_members = [guild.get_member(uid) for uid in waiting_user_ids if guild.get_member(uid)]
            mentions = ", ".join([m.mention for m in waiting_members[:20]])
            if len(waiting_members) > 20:
                mentions += f" _et {len(waiting_members) - 20} autres..._"
            embed.add_field(name=f"⏳ En attente de confirmation ({len(waiting_members)})", value=mentions, inline=False)
    else:
        # Pour sondage classique : juste les non-votants
        non_voted = [m for m in all_members if m.id not in voted_user_ids]
        if non_voted:
            mentions = ", ".join([m.mention for m in non_voted[:20]])
            if len(non_voted) > 20:
                mentions += f" _et {len(non_voted) - 20} autres..._"
            embed.add_field(name=f"❓ Non-votants ({len(non_voted)})", value=mentions, inline=False)

    # Afficher les dates
    event_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
    embed.add_field(name="📅 Événement", value=event_str, inline=True)

    if poll["max_date"]:
        max_str = poll["max_date"].strftime("%d/%m/%Y à %H:%M")
        embed.add_field(name="⏰ Date limite de vote", value=max_str, inline=True)

    # Vérifier si le sondage est fermé
    now = datetime.now(TZ_FR)
    if poll["max_date"] and now > poll["max_date"]:
        embed.set_footer(text="🔒 Le vote est terminé")

    # 🆕 ÉDITER UNIQUEMENT L'EMBED (pas de content)
    await message.edit(embed=embed)

# -------------------- Restore Views --------------------
async def restore_poll_views():
    """Restaure les boutons interactifs après un redémarrage"""
    await bot.wait_until_ready()

    async with db.acquire() as conn:
        # Récupérer tous les sondages actifs (non fermés)
        now = datetime.now(TZ_FR)
        polls = await conn.fetch("""
            SELECT * FROM polls 
            WHERE max_date IS NULL OR max_date > $1
        """, now)

    logging.info(f"🔄 Restauration de {len(polls)} sondages actifs...")

    for poll in polls:
        try:
            channel = bot.get_channel(poll["channel_id"])
            if not channel:
                continue

            try:
                message = await channel.fetch_message(poll["message_id"])
            except discord.NotFound:
                logging.warning(f"Message {poll['message_id']} introuvable, nettoyage...")
                async with db.acquire() as conn:
                    await conn.execute("DELETE FROM polls WHERE id=$1", poll["id"])
                continue

            # Créer la vue appropriée
            if poll["is_presence_poll"]:
                view = PresencePollView(poll["id"])
            else:
                view = PollView(poll["id"], poll["options"])

            # Réattacher la vue au message
            await message.edit(view=view)
            logging.info(f"✅ Sondage #{poll['id']} restauré")

        except Exception as e:
            logging.error(f"Erreur lors de la restauration du sondage {poll['id']}: {e}")

    logging.info("✅ Tous les sondages ont été restaurés")

# -------------------- Commands --------------------
@tree.command(name="poll", description="Créer un sondage")
@app_commands.describe(
    question="La question du sondage",
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
    choix11="Onzième choix",
    choix12="Douzième choix",
    choix13="Treizième choix",
    choix14="Quatorzième choix",
    choix15="Quinzième choix",
    choix16="Seizième choix",
    choix17="Dix-septième choix",
    choix18="Dix-huitième choix",
    choix19="Dix-neuvième choix",
    choix20="Vingtième choix"
)
async def poll_command(
    interaction: discord.Interaction,
    question: str = "Dispo?",
    choix1: str = None,
    choix2: str = None,
    choix3: str = None,
    choix4: str = None,
    choix5: str = None,
    choix6: str = None,
    choix7: str = None,
    choix8: str = None,
    choix9: str = None,
    choix10: str = None,
    choix11: str = None,
    choix12: str = None,
    choix13: str = None,
    choix14: str = None,
    choix15: str = None,
    choix16: str = None,
    choix17: str = None,
    choix18: str = None,
    choix19: str = None,
    choix20: str = None
):
    """Commande pour créer un sondage"""
    choices = [choix1, choix2, choix3, choix4, choix5, choix6, choix7, choix8, choix9, choix10,
               choix11, choix12, choix13, choix14, choix15, choix16, choix17, choix18, choix19, choix20]
    options = [c for c in choices if c]

    # Si aucun choix → sondage de présence
    if not options:
        modal = DateModal(question, [], is_presence=True)
        await interaction.response.send_modal(modal)
        return

    # Si 1 seul choix → erreur
    if len(options) < 2:
        await interaction.response.send_message("❌ Il faut au moins 2 choix pour un sondage classique", ephemeral=True)
        return

    # Sondage classique
    modal = DateModal(question, options, is_presence=False)
    await interaction.response.send_modal(modal)

@tree.command(name="check_polls", description="Vérifie l'état des sondages actifs (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def check_polls(interaction: discord.Interaction):
    """Vérifie l'état des sondages actifs"""
    async with db.acquire() as conn:
        polls = await conn.fetch("SELECT * FROM polls")

    if not polls:
        await interaction.response.send_message("Aucun sondage en base", ephemeral=True)
        return

    msg = "📊 **Sondages en base :**\n"
    now = datetime.now(TZ_FR)
    for p in polls:
        status = "🟢 Actif" if not p["max_date"] or p["max_date"] > now else "🔴 Fermé"
        msg += f"\n{status} ID:{p['id']} - {p['question'][:50]}"

    await interaction.response.send_message(msg, ephemeral=True)

# -------------------- Reminders --------------------
async def send_reminders():
    """Envoie les rappels appropriés selon le type de sondage et la situation"""
    now = datetime.now(TZ_FR)

    async with db.acquire() as conn:
        polls = await conn.fetch("SELECT * FROM polls")

    for poll in polls:
        await check_and_send_reminders(poll, now)


async def check_and_send_reminders(poll, now):
    """Vérifie et envoie les rappels pour un sondage donné"""
    
    # 1. Fermeture du sondage si max_date dépassée
    if poll["max_date"] and now >= poll["max_date"]:
        await close_poll(poll)
        return

    # 2. Rappels pour sondages AVEC max_date
    if poll["max_date"]:
        time_until_deadline = poll["max_date"] - now

        # Rappel J-2 (48h avant) pour les personnes en attente
        if timedelta(hours=47) <= time_until_deadline <= timedelta(hours=49):
            async with db.acquire() as conn:
                already_sent = await conn.fetchrow(
                    "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type='j_minus_2_waiting'",
                    poll["id"]
                )
            
            if not already_sent:
                await send_waiting_reminder(poll, "⏰ **Rappel : Plus que 2 jours !**\n\nTu es toujours en attente pour ce sondage.")
                async with db.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, 'j_minus_2_waiting')",
                        poll["id"]
                    )

        # Rappel J-1 (24h avant) pour les personnes en attente
        elif timedelta(hours=23) <= time_until_deadline <= timedelta(hours=25):
            async with db.acquire() as conn:
                already_sent = await conn.fetchrow(
                    "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type='j_minus_1_waiting'",
                    poll["id"]
                )
            
            if not already_sent:
                await send_waiting_reminder(poll, "🔔 **Rappel : Dernier jour !**\n\nTu es toujours en attente. Confirme ta présence avant la date limite !")
                async with db.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, 'j_minus_1_waiting')",
                        poll["id"]
                    )

    # 3. Rappels pour sondages SANS max_date
    else:
        poll_age = now - poll["created_at"].replace(tzinfo=TZ_FR)

        # Rappel hebdomadaire pour les personnes en attente (sondage de présence)
        if poll["is_presence_poll"]:
            weeks_since_creation = int(poll_age.days / 7)
            
            for week in range(1, weeks_since_creation + 1):
                async with db.acquire() as conn:
                    already_sent = await conn.fetchrow(
                        "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type=$2",
                        poll["id"], f"weekly_waiting_{week}"
                    )
                
                if not already_sent:
                    target_week_date = poll["created_at"].replace(tzinfo=TZ_FR) + timedelta(weeks=week)
                    
                    # Vérifier si on est dans la fenêtre de 1 heure autour de 19h du jour cible
                    if target_week_date.date() == now.date() and 18 <= now.hour <= 20:
                        await send_waiting_reminder(poll, f"📅 **Rappel hebdomadaire**\n\nTu es toujours en attente. Peux-tu confirmer ta présence ?")
                        async with db.acquire() as conn:
                            await conn.execute(
                                "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, $2)",
                                poll["id"], f"weekly_waiting_{week}"
                            )


async def send_waiting_reminder(poll, message_text):
    """Envoie un rappel uniquement aux personnes en attente (⏳)"""
    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except:
        return

    guild = channel.guild

    async with db.acquire() as conn:
        votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll["id"])

    waiting_user_ids = set()
    for v in votes:
        if v["emoji"] == "⏳":
            waiting_user_ids.add(v["user_id"])

    if not waiting_user_ids:
        return

    event_date_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")

    for user_id in waiting_user_ids:
        try:
            member = guild.get_member(user_id)
            if not member or member.bot:
                continue

            msg = f"{message_text}\n\n**{poll['question']}**\n📅 Événement : {event_date_str}\n👉 {message.jump_url}"
            await member.send(msg)
        except:
            pass


async def send_non_voters_reminder(poll, message_text):
    """Envoie un rappel aux non-votants ET aux personnes en attente"""
    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except:
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

    to_notify = []
    for member in guild.members:
        if member.bot:
            continue
        if not channel.permissions_for(member).read_messages:
            continue

        if poll["is_presence_poll"]:
            # Notifier les non-votants ET ceux en attente
            if member.id not in voted_user_ids or member.id in waiting_user_ids:
                to_notify.append(member)
        else:
            if member.id not in voted_user_ids:
                to_notify.append(member)

    event_date_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")

    for member in to_notify:
        try:
            msg = f"{message_text}\n\n**{poll['question']}**\n📅 Événement : {event_date_str}\n👉 {message.jump_url}"
            await member.send(msg)
        except:
            pass


async def close_poll(poll):
    """Ferme un sondage et notifie les non-votants"""
    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except:
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

    if to_notify:
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

async def daily_19h_scheduler():
    """Scheduler qui s'exécute tous les jours à 19h pour envoyer les rappels aux non-votants"""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            now = datetime.now(TZ_FR)
            
            # Calculer le prochain 19h
            target = now.replace(hour=19, minute=0, second=0, microsecond=0)
            if now.hour >= 19:
                target += timedelta(days=1)
            
            # Attendre jusqu'à 19h
            wait_seconds = (target - now).total_seconds()
            logging.info(f"⏰ Prochain rappel quotidien dans {wait_seconds/3600:.1f}h ({target})")
            await asyncio.sleep(wait_seconds)
            
            # Envoyer les rappels pour les non-votants (tous les 2 jours)
            await send_non_voters_biweekly_reminders()
            
        except Exception as e:
            logging.error(f"Erreur dans le scheduler quotidien: {e}")
            await asyncio.sleep(3600)


async def send_non_voters_biweekly_reminders():
    """Envoie un rappel aux non-votants tous les 2 jours à 19h"""
    now = datetime.now(TZ_FR)
    
    async with db.acquire() as conn:
        polls = await conn.fetch("SELECT * FROM polls WHERE max_date IS NULL OR max_date > $1", now)
    
    for poll in polls:
        poll_age = now - poll["created_at"].replace(tzinfo=TZ_FR)
        days_since_creation = poll_age.days
        
        # Vérifier tous les 2 jours (0, 2, 4, 6...)
        if days_since_creation > 0 and days_since_creation % 2 == 0:
            reminder_day = days_since_creation // 2
            
            async with db.acquire() as conn:
                already_sent = await conn.fetchrow(
                    "SELECT * FROM reminders_sent WHERE poll_id=$1 AND reminder_type=$2",
                    poll["id"], f"non_voters_day_{days_since_creation}"
                )
            
            if not already_sent:
                await send_non_voters_reminder(
                    poll,
                    "🔔 **Rappel : N'oublie pas de voter !**\n\nTu n'as pas encore voté pour ce sondage."
                )
                
                async with db.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, $2)",
                        poll["id"], f"non_voters_day_{days_since_creation}"
                    )
                
                logging.info(f"✅ Rappel non-votants envoyé pour le sondage {poll['id']} (jour {days_since_creation})")


# -------------------- Events --------------------
@bot.event
async def on_ready():
    global db
    db = await get_db()
    await init_db()
    await tree.sync()

    # RESTAURER LES VIEWS
    await restore_poll_views()

    # Lancer le scheduler de rappels
    bot.loop.create_task(reminder_scheduler())
    bot.loop.create_task(daily_19h_scheduler())

    logging.info(f"✅ Bot connecté : {bot.user}")

# -------------------- Run Bot --------------------
bot.run(os.getenv("TOKEN_DISCORD"))

