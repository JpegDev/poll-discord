import discord
from collections import defaultdict
from datetime import datetime
from discord.ui import Button, View, Select
from utils.config import Config, is_editor
from utils import database
from utils.poll_utils import update_poll_display
import logging

logger = logging.getLogger(__name__)

MAX_SELECT_OPTIONS = 25  # limite Discord d'un Select


class BasePollView(View):
    """Classe de base pour les vues de sondage"""
    
    def __init__(self, poll_id: int, allow_multiple: bool = False, show_edit: bool = True):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.allow_multiple = allow_multiple
        
        if show_edit:
            edit_button = Button(
                label="Modifier un vote",
                emoji="✏️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"edit_vote_{poll_id}"
            )
            edit_button.callback = self.edit_vote_callback
            self.add_item(edit_button)
    
    async def edit_vote_callback(self, interaction: discord.Interaction):
        """Ouvre la vue pour sélectionner un membre"""
        if not is_editor(interaction):
            await interaction.response.send_message("❌ Tu n'as pas le rôle éditeur de sondage", ephemeral=True)
            return
        
        async with database.db.acquire() as conn:
            poll = await conn.fetchrow("SELECT * FROM polls WHERE id=$1", self.poll_id)
            if not poll:
                await interaction.response.send_message("❌ Sondage introuvable", ephemeral=True)
                return
            
            votes = await conn.fetch("SELECT user_id, emoji FROM votes WHERE poll_id=$1", self.poll_id)
            votes_dict = defaultdict(list)  # choix multiple : plusieurs votes par membre
            for v in votes:
                votes_dict[v["user_id"]].append(v["emoji"])
        
        members_data = []
        channel = interaction.guild.get_channel(poll["channel_id"])
        if channel:
            try:
                channel_obj = await interaction.guild.fetch_channel(poll["channel_id"])
                members = channel_obj.members if hasattr(channel_obj, 'members') else []
                for member in members:
                    if not member.bot:
                        members_data.append((member.id, member.display_name, votes_dict.get(member.id, [])))
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des membres: {e}")
        
        members_data.sort(key=lambda x: x[1].lower())
        
        view = MemberSelectView(self.poll_id, dict(poll), members_data)
        text = "Sélectionnez un membre dont vous voulez modifier le vote:"
        if len(members_data) > MAX_SELECT_OPTIONS:
            text += f"\n⚠️ Liste limitée aux {MAX_SELECT_OPTIONS} premiers membres (ordre alphabétique) sur {len(members_data)}."
        await interaction.response.send_message(
            text,
            view=view,
            ephemeral=True
        )
    
    async def handle_vote(self, interaction: discord.Interaction, emoji: str):
        """Gère un vote (logique commune)"""
        try:
            async with database.db.acquire() as conn:
                deadline = await conn.fetchval("SELECT COALESCE(max_date, event_date) FROM polls WHERE id=$1", self.poll_id)
                if deadline and datetime.now(Config.TZ) >= deadline:
                    await interaction.response.send_message("🔒 Le vote est terminé", ephemeral=True)
                    return

                # DELETE + INSERT atomiques : un clic ne laisse jamais un état à moitié écrit
                async with conn.transaction():
                    already_voted = await conn.fetchval(
                        "SELECT 1 FROM votes WHERE poll_id=$1 AND user_id=$2 AND emoji=$3",
                        self.poll_id, interaction.user.id, emoji
                    )
                    if already_voted:
                        await conn.execute(
                            "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2 AND emoji=$3",
                            self.poll_id, interaction.user.id, emoji
                        )
                        message = "✅ Vote annulé"
                    else:
                        if not self.allow_multiple:
                            await conn.execute(
                                "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                                self.poll_id, interaction.user.id
                            )
                        await conn.execute(
                            "INSERT INTO votes (poll_id, user_id, emoji) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                            self.poll_id, interaction.user.id, emoji
                        )
                        message = "✅ Vote ajouté (choix multiple)" if self.allow_multiple else "✅ Vote enregistré"

            await interaction.response.send_message(message, ephemeral=True)
            logger.info(f"{message}: user={interaction.user.id}, poll={self.poll_id}, emoji={emoji}")
            await update_poll_display(interaction.message, self.poll_id)

        except Exception as e:
            logger.error(f"❌ Erreur lors du vote: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Erreur lors de l'enregistrement du vote", ephemeral=True)


class PollView(BasePollView):
    """Vue pour un sondage classique avec options personnalisées"""
    
    def __init__(self, poll_id: int, options: list, allow_multiple: bool = False, show_edit: bool = True):
        super().__init__(poll_id, allow_multiple, show_edit)

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
    """Vue pour un sondage de présence"""
    
    def __init__(self, poll_id: int, show_edit: bool = True):
        super().__init__(poll_id, allow_multiple=False, show_edit=show_edit)

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


class MemberSelectView(View):
    """Vue avec Select pour choisir un membre à modifier"""
    
    def __init__(self, poll_id: int, poll_data: dict, members_data: list):
        super().__init__(timeout=300)
        self.poll_id = poll_id
        self.poll_data = poll_data
        self.members_data = members_data
        
        options = []
        for member_id, username, current_votes in members_data[:MAX_SELECT_OPTIONS]:
            vote_text = f" → {' '.join(current_votes)}" if current_votes else " → ⏸️ Pas de vote"
            options.append(discord.SelectOption(label=username[:80], value=str(member_id), description=vote_text[:100]))
        
        select = Select(
            placeholder="Sélectionner un membre",
            options=options,
            custom_id=f"member_select_{poll_id}"
        )
        select.callback = self.select_callback
        self.add_item(select)
        
        cancel_btn = Button(
            label="Annuler",
            style=discord.ButtonStyle.danger,
            custom_id=f"cancel_edit_{poll_id}"
        )
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)
    
    async def select_callback(self, interaction: discord.Interaction):
        member_id = int(interaction.data["values"][0])
        member_data = [(mid, name, vote) for mid, name, vote in self.members_data if mid == member_id]
        if member_data:
            await interaction.response.send_message(
                f"📝 Modification du vote de **{member_data[0][1]}** - Utilisez le bouton ci-dessous pour sélectionner le nouveau vote.",
                view=EditVoteView(self.poll_id, self.poll_data, member_data[0]),
                ephemeral=True
            )
    
    async def cancel_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("❌ Opération annulée", ephemeral=True)


class EditVoteView(View):
    """Vue pour modifier un vote avec Select"""
    
    def __init__(self, poll_id: int, poll_data: dict, member_data: tuple):
        super().__init__(timeout=300)
        self.poll_id = poll_id
        self.poll_data = poll_data
        self.member_id, self.member_name, current_votes = member_data
        self.current_votes = current_votes or []

        select_options = []
        if poll_data["is_presence_poll"]:
            vote_options = [
                ("✅ Présent", "✅"),
                ("⏳ En attente", "⏳"),
                ("❌ Absent", "❌")
            ]
        else:
            vote_options = [(opt, Config.EMOJIS[i]) for i, opt in enumerate(poll_data["options"])]

        labels = {emoji: label for label, emoji in vote_options}
        current_label = ", ".join(labels.get(v, v) for v in self.current_votes) or "Aucun"

        for label, emoji in vote_options:
            select_options.append(discord.SelectOption(
                label=label,
                value=emoji,
                default=(emoji in self.current_votes)
            ))

        select_options.append(discord.SelectOption(
            label="🗑️ Supprimer le vote",
            value="DELETE",
            default=not self.current_votes
        ))

        # Choix multiple : on coche/décoche chaque choix, les autres votes du membre sont conservés
        multiple = poll_data.get("allow_multiple") and not poll_data["is_presence_poll"]
        select = Select(
            placeholder=f"Vote actuel: {current_label}"[:150],
            options=select_options,
            max_values=len(select_options) if multiple else 1,
            custom_id=f"edit_vote_select_{poll_id}"
        )
        select.callback = self.select_vote_callback
        self.add_item(select)
        
        cancel_btn = Button(
            label="Annuler",
            style=discord.ButtonStyle.danger,
            custom_id=f"cancel_edit_{poll_id}"
        )
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)
    
    async def select_vote_callback(self, interaction: discord.Interaction):
        # La sélection remplace les votes du membre ; "DELETE" seul (ou rien de coché) = plus de vote
        new_votes = [v for v in interaction.data["values"] if v != "DELETE"]

        try:
            async with database.db.acquire() as conn, conn.transaction():
                await conn.execute(
                    "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                    self.poll_id, self.member_id
                )
                for vote in new_votes:
                    await conn.execute(
                        "INSERT INTO votes (poll_id, user_id, emoji) VALUES ($1, $2, $3)",
                        self.poll_id, self.member_id, vote
                    )
            
            await interaction.response.send_message("✅ Vote modifié avec succès", ephemeral=True)
            
            bot = interaction.client
            channel = bot.get_channel(self.poll_data["channel_id"])
            if channel:
                try:
                    message = await channel.fetch_message(self.poll_data["message_id"])
                    await update_poll_display(message, self.poll_id)
                except Exception:
                    pass
                    
        except Exception as e:
            logger.error(f"❌ Erreur lors de la modification: {e}")
            await interaction.response.send_message("❌ Erreur lors de la modification", ephemeral=True)
    
    async def cancel_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("❌ Opération annulée", ephemeral=True)
