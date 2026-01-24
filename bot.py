import os
import asyncio
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# -------------------- Configuration --------------------
class Config:
    """Configuration centralisée du bot"""
    TZ = ZoneInfo("Europe/Paris")
    EMOJIS = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮", "🇯",
              "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷", "🇸", "🇹"]
    MAX_OPTIONS = 20
    MAX_CONTENT_LENGTH = 2000
    MAX_MENTIONS_DISPLAY = 20
    REMINDER_CHECK_INTERVAL = 3600  # 1 heure
    DAILY_REMINDER_HOUR = 19
    MAX_EVENT_DAYS_AHEAD = 730  # 2 ans max
    
    # Fenêtres de rappel (en heures)
    REMINDER_J_MINUS_2_MIN = 47
    REMINDER_J_MINUS_2_MAX = 49
    REMINDER_J_MINUS_1_MIN = 23
    REMINDER_J_MINUS_1_MAX = 25

# -------------------- Intents --------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -------------------- Database --------------------
DATABASE_URL = os.getenv("DATABASE_URL")
db = None

async def get_db():
    """Crée le pool de connexions à la base de données"""
    try:
        return await asyncpg.create_pool(DATABASE_URL)
    except Exception as e:
        logger.error(f"❌ Erreur de connexion à la base de données: {e}")
        raise

async def init_db():
    """Initialise les tables de la base de données avec migration"""
    try:
        async with db.acquire() as conn:
            # Créer les tables de base
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
                    allow_multiple BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            
            # Vérifier si la table votes existe avec l'ancienne structure
            old_structure = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints 
                    WHERE table_name='votes' 
                    AND constraint_type='PRIMARY KEY'
                    AND constraint_name='votes_pkey'
                )
            """)
            
            if old_structure:
                logger.info("🔄 Migration de la table votes détectée...")
                
                # Créer une nouvelle table avec la nouvelle structure
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS votes_new (
                        poll_id INTEGER REFERENCES polls(id) ON DELETE CASCADE,
                        user_id BIGINT,
                        emoji TEXT,
                        PRIMARY KEY (poll_id, user_id, emoji)
                    );
                """)
                
                # Copier les données existantes
                await conn.execute("""
                    INSERT INTO votes_new (poll_id, user_id, emoji)
                    SELECT poll_id, user_id, emoji FROM votes
                    ON CONFLICT DO NOTHING;
                """)
                
                # Supprimer l'ancienne table et renommer la nouvelle
                await conn.execute("DROP TABLE votes;")
                await conn.execute("ALTER TABLE votes_new RENAME TO votes;")
                
                logger.info("✅ Migration de la table votes terminée")
            else:
                # Créer directement avec la nouvelle structure
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS votes (
                        poll_id INTEGER REFERENCES polls(id) ON DELETE CASCADE,
                        user_id BIGINT,
                        emoji TEXT,
                        PRIMARY KEY (poll_id, user_id, emoji)
                    );
                """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders_sent (
                    poll_id INTEGER REFERENCES polls(id) ON DELETE CASCADE,
                    sent_at TIMESTAMP DEFAULT NOW(),
                    reminder_type TEXT
                );
            """)
            
            # Ajouter les colonnes manquantes si nécessaire
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
                    
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                  WHERE table_name='polls' AND column_name='allow_multiple') THEN
                        ALTER TABLE polls ADD COLUMN allow_multiple BOOLEAN DEFAULT FALSE;
                    END IF;
                END $$;
            """)
            
        logger.info("✅ Base de données initialisée")
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'initialisation de la DB: {e}")
        raise

# -------------------- Views --------------------
class BasePollView(View):
    """Classe de base pour les vues de sondage"""
    
    def __init__(self, poll_id: int, allow_multiple: bool = False):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.allow_multiple = allow_multiple
    
    async def handle_vote(self, interaction: discord.Interaction, emoji: str):
        """Gère un vote (logique commune)"""
        try:
            async with db.acquire() as conn:
                # Récupérer tous les votes de l'utilisateur pour ce sondage
                existing_votes = await conn.fetch(
                    "SELECT emoji FROM votes WHERE poll_id=$1 AND user_id=$2",
                    self.poll_id, interaction.user.id
                )
                
                existing_emojis = [v["emoji"] for v in existing_votes]
                
                # Si l'utilisateur a déjà voté pour cette option, on l'annule
                if emoji in existing_emojis:
                    await conn.execute(
                        "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2 AND emoji=$3",
                        self.poll_id, interaction.user.id, emoji
                    )
                    await interaction.response.send_message("✅ Vote annulé", ephemeral=True)
                    logger.info(f"Vote annulé: user={interaction.user.id}, poll={self.poll_id}, emoji={emoji}")
                else:
                    # Si vote unique (single choice), supprimer tous les anciens votes
                    if not self.allow_multiple:
                        await conn.execute(
                            "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                            self.poll_id, interaction.user.id
                        )
                    
                    # Ajouter le nouveau vote
                    await conn.execute("""
                        INSERT INTO votes (poll_id, user_id, emoji) 
                        VALUES ($1, $2, $3)
                        ON CONFLICT DO NOTHING
                    """, self.poll_id, interaction.user.id, emoji)
                    
                    if self.allow_multiple:
                        await interaction.response.send_message("✅ Vote ajouté (choix multiple)", ephemeral=True)
                    else:
                        await interaction.response.send_message("✅ Vote enregistré", ephemeral=True)
                    
                    logger.info(f"Vote enregistré: user={interaction.user.id}, poll={self.poll_id}, emoji={emoji}")

            await update_poll_display(interaction.message, self.poll_id)
        
        except asyncpg.PostgresError as e:
            logger.error(f"❌ Erreur DB lors du vote: {e}")
            await interaction.response.send_message("❌ Erreur lors de l'enregistrement du vote", ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"❌ Erreur Discord lors du vote: {e}")
        except Exception as e:
            logger.error(f"❌ Erreur inattendue lors du vote: {e}")

class PollView(BasePollView):
    """Vue pour un sondage classique avec options personnalisées"""
    
    def __init__(self, poll_id: int, options: list, allow_multiple: bool = False):
        super().__init__(poll_id, allow_multiple)

        for i, option in enumerate(options[:Config.MAX_OPTIONS]):
            button = Button(
                label=option,
                emoji=Config.EMOJIS[i],
                style=discord.ButtonStyle.primary,
                custom_id=f"poll_{poll_id}_{Config.EMOJIS[i]}"
            )
            button.callback = self.make_callback(Config.EMOJIS[i])
            self.add_item(button)

    def make_callback(self, emoji):
        async def callback(interaction: discord.Interaction):
            await self.handle_vote(interaction, emoji)
        return callback

class PresencePollView(BasePollView):
    """Vue pour un sondage de présence (Présent/En attente/Absent) - toujours choix unique"""
    
    def __init__(self, poll_id: int):
        super().__init__(poll_id, allow_multiple=False)  # Toujours choix unique

        buttons = [
            ("Présent", "✅", discord.ButtonStyle.success),
            ("En attente", "⏳", discord.ButtonStyle.secondary),
            ("Absent", "❌", discord.ButtonStyle.danger)
        ]

        for label, emoji, style in buttons:
            btn = Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"presence_{poll_id}_{emoji}"
            )
            btn.callback = self.make_callback(emoji)
            self.add_item(btn)

    def make_callback(self, emoji):
        async def callback(interaction: discord.Interaction):
            await self.handle_vote(interaction, emoji)
        return callback

# -------------------- Modal --------------------
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

            # Validations
            validation_error = self._validate_dates(event_dt, max_dt)
            if validation_error:
                await interaction.response.send_message(validation_error, ephemeral=True)
                return

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

# -------------------- Functions --------------------
async def create_poll(interaction: discord.Interaction, question: str, options: list, 
                     is_presence: bool, event_date: datetime, max_date: datetime = None,
                     allow_multiple: bool = False):
    """Crée un sondage en base et envoie le message"""
    try:
        # Créer la vue initiale
        if is_presence:
            view = PresencePollView(0)
        else:
            view = PollView(0, options, allow_multiple)

        # Envoyer le message
        await interaction.response.send_message(content="📊 _Chargement..._", view=view)
        message = await interaction.original_response()

        # Enregistrer en base
        async with db.acquire() as conn:
            poll_id = await conn.fetchval("""
                INSERT INTO polls (message_id, channel_id, question, options, is_presence_poll, 
                                  event_date, max_date, allow_multiple)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            """, message.id, interaction.channel_id, question, options, is_presence, 
               event_date, max_date, allow_multiple)

        logger.info(f"✅ Sondage créé: id={poll_id}, question='{question[:50]}', multiple={allow_multiple}")

        # Recréer la vue avec le bon ID
        if is_presence:
            view = PresencePollView(poll_id)
        else:
            view = PollView(poll_id, options, allow_multiple)

        await update_poll_display(message, poll_id)
        await message.edit(view=view)
        
    except asyncpg.PostgresError as e:
        logger.error(f"❌ Erreur DB lors de la création du sondage: {e}")
        await interaction.followup.send("❌ Erreur lors de la création du sondage", ephemeral=True)
    except discord.HTTPException as e:
        logger.error(f"❌ Erreur Discord lors de la création du sondage: {e}")
    except Exception as e:
        logger.error(f"❌ Erreur inattendue lors de la création du sondage: {e}")

async def update_poll_display(message: discord.Message, poll_id: int):
    """Met à jour l'affichage d'un sondage"""
    try:
        async with db.acquire() as conn:
            poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", poll_id)
            if not poll:
                logger.warning(f"Sondage {poll_id} introuvable")
                return

            votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll_id)

        # Organiser les votes
        vote_counts = defaultdict(list)
        user_votes = defaultdict(list)  # Pour gérer le vote multiple
        
        for vote in votes:
            vote_counts[vote["emoji"]].append(vote["user_id"])
            user_votes[vote["user_id"]].append(vote["emoji"])

        # Construire le contenu
        content = _build_poll_content(poll, vote_counts, user_votes, message.guild, message.channel, votes)

        # Vérifier la limite Discord
        if len(content) > Config.MAX_CONTENT_LENGTH:
            content = content[:Config.MAX_CONTENT_LENGTH - 3] + "..."

        await message.edit(content=content)
        
    except discord.NotFound:
        logger.warning(f"Message {message.id} introuvable lors de la mise à jour")
    except discord.HTTPException as e:
        logger.error(f"❌ Erreur Discord lors de la mise à jour: {e}")
    except Exception as e:
        logger.error(f"❌ Erreur lors de la mise à jour du sondage {poll_id}: {e}")

def _build_poll_content(poll, vote_counts, user_votes, guild, channel, votes) -> str:
    """Construit le contenu textuel d'un sondage"""
    mode_text = ""
    if not poll["is_presence_poll"]:
        mode_text = " 🔘 Choix unique" if not poll["allow_multiple"] else " ☑️ Choix multiple"
    
    content_parts = [f"# 📊 {poll['question']}{mode_text}\n"]

    # Afficher les votes
    if poll["is_presence_poll"]:
        _add_presence_votes(content_parts, vote_counts)
    else:
        _add_option_votes(content_parts, poll["options"], vote_counts, poll["allow_multiple"])

    content_parts.append("")

    # Afficher les non-votants et personnes en attente
    _add_non_voters_section(content_parts, poll, guild, channel, votes, vote_counts)

    # Afficher les dates
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
    all_members = [m for m in guild.members if not m.bot and channel.permissions_for(m).read_messages]
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

# -------------------- Restore Views --------------------
async def restore_poll_views():
    """Restaure les boutons interactifs après un redémarrage"""
    await bot.wait_until_ready()

    try:
        async with db.acquire() as conn:
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
                    async with db.acquire() as conn:
                        await conn.execute("DELETE FROM polls WHERE id=$1", poll["id"])
                    continue

                if poll["is_presence_poll"]:
                    view = PresencePollView(poll["id"])
                else:
                    view = PollView(poll["id"], poll["options"], poll["allow_multiple"])
                
                await message.edit(view=view)
                restored += 1

            except Exception as e:
                logger.error(f"❌ Erreur lors de la restauration du sondage {poll['id']}: {e}")

        logger.info(f"✅ {restored}/{len(polls)} sondages restaurés avec succès")
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la restauration des vues: {e}")

# -------------------- Commands --------------------
@tree.command(name="poll", description="Créer un sondage")
@app_commands.describe(
    question="La question du sondage",
    single="Choix unique (oui/non) - Par défaut: non (choix multiple autorisé)",
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
    single: bool = False,
    choix1: str = None, choix2: str = None, choix3: str = None, choix4: str = None,
    choix5: str = None, choix6: str = None, choix7: str = None, choix8: str = None,
    choix9: str = None, choix10: str = None, choix11: str = None, choix12: str = None,
    choix13: str = None, choix14: str = None, choix15: str = None, choix16: str = None,
    choix17: str = None, choix18: str = None, choix19: str = None, choix20: str = None
):
    """Commande pour créer un sondage"""
    choices = [choix1, choix2, choix3, choix4, choix5, choix6, choix7, choix8, choix9, choix10,
               choix11, choix12, choix13, choix14, choix15, choix16, choix17, choix18, choix19, choix20]
    options = [c for c in choices if c]

    if not options:
        # Sondage de présence (toujours choix unique)
        modal = DateModal(question, [], is_presence=True, allow_multiple=False)
        await interaction.response.send_modal(modal)
        return

    if len(options) < 2:
        await interaction.response.send_message("❌ Il faut au moins 2 choix pour un sondage classique", ephemeral=True)
        return

    # Sondage classique - utiliser le paramètre 'single' (inversé car par défaut = choix multiple)
    allow_multiple = not single
    modal = DateModal(question, options, is_presence=False, allow_multiple=allow_multiple)
    await interaction.response.send_modal(modal)

@tree.command(name="check_polls", description="Vérifie l'état des sondages actifs (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def check_polls(interaction: discord.Interaction):
    """Vérifie l'état des sondages actifs"""
    try:
        async with db.acquire() as conn:
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

# -------------------- Reminders --------------------
async def send_reminders():
    """Envoie les rappels appropriés selon le type de sondage et la situation"""
    try:
        now = datetime.now(Config.TZ)

        async with db.acquire() as conn:
            polls = await conn.fetch("SELECT * FROM polls")

        for poll in polls:
            try:
                await check_and_send_reminders(poll, now)
            except Exception as e:
                logger.error(f"❌ Erreur lors du traitement des rappels pour le sondage {poll['id']}: {e}")
                
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi des rappels: {e}")

async def check_and_send_reminders(poll, now):
    """Vérifie et envoie les rappels pour un sondage donné"""
    if poll["max_date"] and now >= poll["max_date"]:
        await close_poll(poll)
        return

    if poll["max_date"]:
        time_until_deadline = poll["max_date"] - now

        # Rappel J-2
        if timedelta(hours=Config.REMINDER_J_MINUS_2_MIN) <= time_until_deadline <= timedelta(hours=Config.REMINDER_J_MINUS_2_MAX):
            if not await _reminder_already_sent(poll["id"], 'j_minus_2_waiting'):
                await send_waiting_reminder(poll, "⏰ **Rappel : Plus que 2 jours !**\n\nTu es toujours en attente pour ce sondage.")
                await _mark_reminder_sent(poll["id"], 'j_minus_2_waiting')
                logger.info(f"📨 Rappel J-2 envoyé pour le sondage {poll['id']}")

        # Rappel J-1
        elif timedelta(hours=Config.REMINDER_J_MINUS_1_MIN) <= time_until_deadline <= timedelta(hours=Config.REMINDER_J_MINUS_1_MAX):
            if not await _reminder_already_sent(poll["id"], 'j_minus_1_waiting'):
                await send_waiting_reminder(poll, "🔔 **Rappel : Dernier jour !**\n\nTu es toujours en attente. Confirme ta présence avant la date limite !")
                await _mark_reminder_sent(poll["id"], 'j_minus_1_waiting')
                logger.info(f"📨 Rappel J-1 envoyé pour le sondage {poll['id']}")
    else:
        # Rappels hebdomadaires pour sondages sans max_date
        if poll["is_presence_poll"]:
            poll_age = now - poll["created_at"].replace(tzinfo=Config.TZ)
            weeks_since_creation = int(poll_age.days / 7)
            
            for week in range(1, weeks_since_creation + 1):
                if not await _reminder_already_sent(poll["id"], f"weekly_waiting_{week}"):
                    target_week_date = poll["created_at"].replace(tzinfo=Config.TZ) + timedelta(weeks=week)
                    
                    if target_week_date.date() == now.date() and 18 <= now.hour <= 20:
                        await send_waiting_reminder(poll, f"📅 **Rappel hebdomadaire**\n\nTu es toujours en attente. Peux-tu confirmer ta présence ?")
                        await _mark_reminder_sent(poll["id"], f"weekly_waiting_{week}")
                        logger.info(f"📨 Rappel hebdomadaire semaine {week} envoyé pour le sondage {poll['id']}")

async def _reminder_already_sent(poll_id: int, reminder_type: str) -> bool:
    """Vérifie si un rappel a déjà été envoyé"""
    try:
        async with db.acquire() as conn:
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
        async with db.acquire() as conn:
            await conn.execute(
                "INSERT INTO reminders_sent (poll_id, reminder_type) VALUES ($1, $2)",
                poll_id, reminder_type
            )
    except Exception as e:
        logger.error(f"❌ Erreur lors du marquage du rappel: {e}")

async def send_waiting_reminder(poll, message_text):
    """Envoie un rappel uniquement aux personnes en attente (⏳)"""
    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except discord.NotFound:
        logger.warning(f"Message {poll['message_id']} introuvable pour le rappel")
        return
    except Exception as e:
        logger.error(f"❌ Erreur lors de la récupération du message: {e}")
        return

    guild = channel.guild

    try:
        async with db.acquire() as conn:
            votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", poll["id"])

        waiting_user_ids = {v["user_id"] for v in votes if v["emoji"] == "⏳"}

        if not waiting_user_ids:
            logger.info(f"Aucune personne en attente pour le sondage {poll['id']}")
            return

        event_date_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
        sent_count = 0

        for user_id in waiting_user_ids:
            try:
                member = guild.get_member(user_id)
                if not member or member.bot:
                    continue

                msg = f"{message_text}\n\n**{poll['question']}**\n📅 Événement : {event_date_str}\n👉 {message.jump_url}"
                await member.send(msg)
                sent_count += 1
            except discord.Forbidden:
                logger.warning(f"Impossible d'envoyer un DM à {user_id} (DM fermés)")
            except Exception as e:
                logger.error(f"❌ Erreur lors de l'envoi du DM à {user_id}: {e}")

        logger.info(f"📨 Rappel envoyé à {sent_count} personnes en attente pour le sondage {poll['id']}")
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi des rappels en attente: {e}")

async def send_non_voters_reminder(poll, message_text):
    """Envoie un rappel aux non-votants ET aux personnes en attente"""
    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except discord.NotFound:
        logger.warning(f"Message {poll['message_id']} introuvable pour le rappel")
        return
    except Exception as e:
        logger.error(f"❌ Erreur lors de la récupération du message: {e}")
        return

    guild = channel.guild

    try:
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
                if member.id not in voted_user_ids or member.id in waiting_user_ids:
                    to_notify.append(member)
            else:
                if member.id not in voted_user_ids:
                    to_notify.append(member)

        event_date_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
        sent_count = 0

        for member in to_notify:
            try:
                msg = f"{message_text}\n\n**{poll['question']}**\n📅 Événement : {event_date_str}\n👉 {message.jump_url}"
                await member.send(msg)
                sent_count += 1
            except discord.Forbidden:
                logger.warning(f"Impossible d'envoyer un DM à {member.id} (DM fermés)")
            except Exception as e:
                logger.error(f"❌ Erreur lors de l'envoi du DM à {member.id}: {e}")

        logger.info(f"📨 Rappel envoyé à {sent_count} non-votants pour le sondage {poll['id']}")
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi des rappels non-votants: {e}")

async def close_poll(poll):
    """Ferme un sondage et notifie les non-votants"""
    if await _reminder_already_sent(poll["id"], 'closed'):
        return

    channel = bot.get_channel(poll["channel_id"])
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
                if member.id not in voted_user_ids or member.id in waiting_user_ids:
                    to_notify.append(member)
            else:
                if member.id not in voted_user_ids:
                    to_notify.append(member)

        if to_notify:
            await message.edit(view=None)
            await update_poll_display(message, poll["id"])

            event_date_str = poll["event_date"].strftime("%d/%m/%Y à %H:%M")
            sent_count = 0

            for member in to_notify:
                try:
                    msg = f"🔒 **Le vote est terminé !**\n\n**{poll['question']}**\n📅 Événement : {event_date_str}\n"

                    if poll["is_presence_poll"] and member.id in waiting_user_ids:
                        msg += "\n⏳ Tu étais en attente et n'as pas confirmé ta présence."
                    else:
                        msg += "\n❌ Tu n'as pas voté à temps."

                    msg += f"\n\n👉 {message.jump_url}"

                    await member.send(msg)
                    sent_count += 1
                except discord.Forbidden:
                    logger.warning(f"Impossible d'envoyer un DM à {member.id} (DM fermés)")
                except Exception as e:
                    logger.error(f"❌ Erreur lors de l'envoi du DM à {member.id}: {e}")

            logger.info(f"🔒 Sondage {poll['id']} fermé, {sent_count} notifications envoyées")

        await _mark_reminder_sent(poll["id"], 'closed')
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la fermeture du sondage {poll['id']}: {e}")

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

async def send_non_voters_biweekly_reminders():
    """Envoie un rappel aux non-votants tous les 2 jours à 19h"""
    try:
        now = datetime.now(Config.TZ)
        
        async with db.acquire() as conn:
            polls = await conn.fetch("SELECT * FROM polls WHERE max_date IS NULL OR max_date > $1", now)
        
        for poll in polls:
            try:
                poll_age = now - poll["created_at"].replace(tzinfo=Config.TZ)
                days_since_creation = poll_age.days
                
                if days_since_creation > 0 and days_since_creation % 2 == 0:
                    if not await _reminder_already_sent(poll["id"], f"non_voters_day_{days_since_creation}"):
                        await send_non_voters_reminder(
                            poll,
                            "🔔 **Rappel : N'oublie pas de voter !**\n\nTu n'as pas encore voté pour ce sondage."
                        )
                        await _mark_reminder_sent(poll["id"], f"non_voters_day_{days_since_creation}")
                        logger.info(f"✅ Rappel non-votants envoyé pour le sondage {poll['id']} (jour {days_since_creation})")
                        
            except Exception as e:
                logger.error(f"❌ Erreur lors du rappel bi-hebdomadaire pour le sondage {poll['id']}: {e}")
                
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi des rappels bi-hebdomadaires: {e}")

# -------------------- Events --------------------
@bot.event
async def on_ready():
    global db
    
    try:
        db = await get_db()
        await init_db()
        await tree.sync()

        await restore_poll_views()

        bot.loop.create_task(reminder_scheduler())
        bot.loop.create_task(daily_19h_scheduler())

        logger.info(f"✅ Bot connecté : {bot.user}")
    except Exception as e:
        logger.error(f"❌ Erreur critique lors du démarrage: {e}")
        raise

# -------------------- Run Bot --------------------
if __name__ == "__main__":
    token = os.getenv("TOKEN_DISCORD")
    if not token:
        logger.error("❌ TOKEN_DISCORD non défini dans les variables d'environnement")
        exit(1)
    
    bot.run(token)