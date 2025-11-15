import os
import asyncio
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # 🇫🇷 Gestion de la France

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
        logging.info("✅ Tables vérifiées.")


# -------------------- Classes pour les boutons --------------------
class PollButton(Button):
    def __init__(self, label, emoji, poll_id, db):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            emoji=emoji,
            custom_id=f"poll_{poll_id}_{emoji}"
        )
        self.poll_id = poll_id
        self.db = db

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        emoji_str = str(self.emoji)

        async with self.db.acquire() as conn:
            existing_vote = await conn.fetchrow(
                "SELECT emoji FROM votes WHERE poll_id=$1 AND user_id=$2",
                self.poll_id, user_id
            )

            if existing_vote:
                if existing_vote["emoji"] == emoji_str:
                    await conn.execute(
                        "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                        self.poll_id, user_id
                    )
                else:
                    await conn.execute(
                        "UPDATE votes SET emoji=$1 WHERE poll_id=$2 AND user_id=$3",
                        emoji_str, self.poll_id, user_id
                    )
            else:
                await conn.execute(
                    "INSERT INTO votes (poll_id, user_id, emoji) VALUES ($1, $2, $3)",
                    self.poll_id, user_id, emoji_str
                )

        async with self.db.acquire() as conn:
            poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", self.poll_id)
            votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", self.poll_id)

        voted_user_ids = set()
        results = {}
        for v in votes:
            results.setdefault(v["emoji"], []).append(v["user_id"])
            voted_user_ids.add(v["user_id"])

        channel = interaction.channel
        guild = channel.guild
        non_voters = [
            m for m in guild.members
            if not m.bot and m.id not in voted_user_ids
            and channel.permissions_for(m).read_messages
        ]

        emojis = [chr(0x1F1E6 + i) for i in range(len(poll["options"]))]
        lines = [f"📊 **{poll['question']}**\n"]
        for i, opt in enumerate(poll["options"]):
            emoji = emojis[i]
            voters = results.get(emoji, [])
            if voters:
                mentions = ", ".join(f"<@{uid}>" for uid in voters)
                lines.append(f"{emoji} **{opt}** ({len(voters)} votes): {mentions}\n")
            else:
                lines.append(f"{emoji} **{opt}** (0 vote)\n")

        if non_voters:
            mentions = ", ".join(f"<@{m.id}>" for m in non_voters)
            lines.append(f"\n👥 **Non-votants ({len(non_voters)})** : {mentions}\n")
        else:
            lines.append("\n👥 **Non-votants** : 0\n")

        await interaction.message.edit(
            content="\n".join(lines) + "\n\u200b",
            embeds=[],
            allowed_mentions=discord.AllowedMentions(users=True)
        )
        await interaction.response.defer()


class PollView(View):
    def __init__(self, poll_id, options, db):
        super().__init__(timeout=None)
        for i, opt in enumerate(options):
            emoji = chr(0x1F1E6 + i)
            self.add_item(PollButton(opt, emoji, poll_id, db))


# -------------------- Bot Ready --------------------
@bot.event
async def on_ready():
    global db
    db = await get_db()
    await init_db()
    await tree.sync()
    logging.info(f"🟢 Connecté en tant que {bot.user}")

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

            bot.add_view(PollView(poll["id"], poll["options"], db), message_id=poll["message_id"])

    bot.loop.create_task(rappel_sondages_scheduler())
    logging.info("⏳ Scheduler des rappels lancé")


# -------------------- Slash Command /poll --------------------
@tree.command(name="poll", description="Créer un sondage avec jusqu'à 20 choix (boutons)")
async def poll(interaction: discord.Interaction,
               question: str,
               choix1: str,
               choix2: str,
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

    if len(options) < 2:
        return await interaction.response.send_message("❌ Il faut au moins 2 options.", ephemeral=True)

    embed = discord.Embed(
        title="📊 Sondage",
        description=f"**{question}**\n\n" + "\n".join(
            f"{chr(0x1F1E6+i)} {opt}" for i, opt in enumerate(options)
        ),
        color=discord.Color.blurple()
    )

    await interaction.response.send_message("⏳ Création du sondage...", ephemeral=True)
    message = await interaction.channel.send(embed=embed)

    async with db.acquire() as conn:
        poll_id = (await conn.fetchrow(
            "INSERT INTO polls (message_id, channel_id, question, options) VALUES ($1,$2,$3,$4) RETURNING id",
            message.id, interaction.channel.id, question, options
        ))["id"]

    view = PollView(poll_id, options, db)
    await message.edit(view=view)

    await interaction.followup.send(f"Sondage créé → {message.jump_url}", ephemeral=True)


# -------------------- Rappels automatiques --------------------
async def rappel_sondages():
    logging.info("📬 Envoi des rappels...")
    async with db.acquire() as conn:
        polls = await conn.fetch("SELECT * FROM polls")
        rappels = defaultdict(list)

        for poll in polls:
            channel = bot.get_channel(poll["channel_id"])
            if not channel:
                continue

            try:
                message = await channel.fetch_message(poll["message_id"])
            except discord.NotFound:
                continue

            guild = channel.guild
            vote_ids = {row["user_id"] for row in await conn.fetch(
                "SELECT user_id FROM votes WHERE poll_id=$1", poll["id"]
            )}

            for member in guild.members:
                if member.bot or member.id in vote_ids:
                    continue
                if channel.permissions_for(member).read_messages:
                    rappels[member.id].append((poll["question"], message.jump_url))

        for uid, items in rappels.items():
            user = bot.get_user(uid)
            if not user:
                continue
            try:
                await user.send(
                    "👋 Tu n’as pas encore voté :\n" +
                    "\n".join(f"• **{q}** → {u}" for q, u in items)
                )
            except:
                pass


# -------------------- Toutes les 48h à 19h 🇫🇷 --------------------
async def rappel_sondages_scheduler():
    await bot.wait_until_ready()
    tz_fr = ZoneInfo("Europe/Paris")

    while not bot.is_closed():
        now = datetime.now(tz_fr)
        next_run = now.replace(hour=19, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        wait = (next_run - now).total_seconds()
        logging.info(f"⏱️ Prochain rappel : {next_run}")

        await asyncio.sleep(wait)
        await rappel_sondages()
        await asyncio.sleep(48 * 3600)


# -------------------- Run Bot --------------------
bot.run(os.getenv("TOKEN_DISCORD"))
