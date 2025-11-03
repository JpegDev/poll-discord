import os
import asyncio
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View

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
        print("✅ Tables vérifiées.")

# -------------------- Classes pour les boutons --------------------
class PollButton(Button):
    def __init__(self, label, emoji, poll_id, db):
        super().__init__(label=label, style=discord.ButtonStyle.primary, emoji=emoji)
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

        # Récupérer tous les votes et le sondage
        async with self.db.acquire() as conn:
            poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", self.poll_id)
            votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", self.poll_id)

        results = {}
        for v in votes:
            results.setdefault(v["emoji"], []).append(v["user_id"])
        channel = interaction.channel
        guild = channel.guild
        non_voters = [
            m for m in guild.members
            if not m.bot
            and m.id not in voted_user_ids
            and channel.permissions_for(m).read_messages
        ]
        emojis = [chr(0x1F1E6 + i) for i in range(len(poll["options"]))]
        lines = [f"📊 **{poll['question']}**\n"]
        for i, opt in enumerate(poll["options"]):
            emoji = emojis[i]
            voters = results.get(emoji, [])
            if voters:
                mentions = ", ".join(f"<@{uid}>" for uid in voters)
                lines.append(f"{emoji} **{opt}** ({len(voters)} votes): {mentions}")
            else:
                lines.append(f"{emoji} **{opt}** (0 vote)")
        lines.append(f"\n👥 **Non-votants** : {len(non_voters)}")
        new_content = "\n".join(lines)

        await interaction.message.edit(
            content=new_content,
            allowed_mentions=discord.AllowedMentions(users=True)
        )
        await interaction.response.defer()

class PollView(View):
    def __init__(self, poll_id, options, db):
        super().__init__(timeout=None)
        emojis = [chr(0x1F1E6 + i) for i in range(len(options))]
        for emoji, opt in zip(emojis, options):
            self.add_item(PollButton(label=opt, emoji=emoji, poll_id=poll_id, db=db))

# -------------------- Bot Ready --------------------
@bot.event
async def on_ready():
    global db
    db = await get_db()
    await init_db()
    await tree.sync()
    print(f"✅ Connecté en tant que {bot.user}")

# -------------------- Slash Command /poll --------------------
@tree.command(name="poll", description="Créer un sondage avec jusqu'à 20 choix (boutons)")
@app_commands.describe(
    question="La question du sondage",
    choix1="Option 1",
    choix2="Option 2",
    choix3="Option 3",
    choix4="Option 4",
    choix5="Option 5",
    choix6="Option 6",
    choix7="Option 7",
    choix8="Option 8",
    choix9="Option 9",
    choix10="Option 10",
    choix11="Option 11",
    choix12="Option 12",
    choix13="Option 13",
    choix14="Option 14",
    choix15="Option 15",
    choix16="Option 16",
    choix17="Option 17",
    choix18="Option 18",
    choix19="Option 19",
    choix20="Option 20"
)
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
    ] if c is not None]

    if len(options) < 2:
        await interaction.response.send_message("❌ Il faut au moins deux options.", ephemeral=True)
        return

    emojis = [chr(0x1F1E6 + i) for i in range(len(options))]
    description = "\n".join(f"{emojis[i]} {opt}" for i, opt in enumerate(options))
    embed = discord.Embed(
        title="📊 Sondage",
        description=f"**{question}**\n\n{description}",
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Créé par {interaction.user.display_name}")

    await interaction.response.defer()
    message = await interaction.channel.send(embed=embed)

    async with db.acquire() as conn:
        poll_record = await conn.fetchrow(
            "INSERT INTO polls (message_id, channel_id, question, options) VALUES ($1, $2, $3, $4) RETURNING id",
            message.id, interaction.channel.id, question, options
        )
        poll_id = poll_record["id"]

    # Ajouter les boutons
    view = PollView(poll_id, options, db)
    await message.edit(view=view)

    await interaction.followup.send(f"Sondage créé ici : {message.jump_url}")

# -------------------- Rappel automatique --------------------
@tasks.loop(hours=1)
async def rappel_sondages():
    print("📬 Envoi des rappels de sondages...")
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

            guild = channel.guild
            choix_multiple = len(poll["options"]) > 2

            votes_data = await conn.fetch("SELECT user_id FROM votes WHERE poll_id=$1", poll["id"])
            voter_ids = {v["user_id"] for v in votes_data}

            for member in guild.members:
                # ⚙️ Filtrer : bots, votants, et ceux sans accès au salon
                if (
                    member.bot
                    or member.id in voter_ids
                    or not channel.permissions_for(member).read_messages
                ):
                    continue

                # Si choix multiple, rappeler uniquement s’il n’y a aucun vote du tout
                if choix_multiple and voter_ids:
                    continue

                try:
                    await member.send(
                        f"👋 Tu n’as pas encore voté au sondage : **{poll['question']}**\n👉 {message.jump_url}"
                    )
                except discord.Forbidden:
                    pass

@rappel_sondages.before_loop
async def before_rappel():
    await bot.wait_until_ready()

# -------------------- Démarrage --------------------
bot.run(os.getenv("TOKEN_DISCORD"))