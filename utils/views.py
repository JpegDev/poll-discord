import discord
from discord.ui import Button, View, Modal, TextInput, Select
from utils.config import Config, is_editor
from utils import database
from utils.poll_utils import update_poll_display
import logging

logger = logging.getLogger(__name__)


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
            votes_dict = {v["user_id"]: v["emoji"] for v in votes}
        
        members_data = []
        channel = interaction.guild.get_channel(poll["channel_id"])
        if channel:
            try:
                channel_obj = await interaction.guild.fetch_channel(poll["channel_id"])
                members = channel_obj.members if hasattr(channel_obj, 'members') else []
                for member in members:
                    if not member.bot:
                        current_vote = votes_dict.get(member.id)
                        members_data.append((member.id, member.display_name, current_vote))
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des membres: {e}")
        
        members_data.sort(key=lambda x: x[1].lower())
        
        view = MemberSelectView(self.poll_id, dict(poll), members_data)
        await interaction.response.send_message(
            "Sélectionnez un membre dont vous voulez modifier le vote:",
            view=view,
            ephemeral=True
        )
    
    async def handle_vote(self, interaction: discord.Interaction, emoji: str):
        """Gère un vote (logique commune)"""
        try:
            async with database.db.acquire() as conn:
                existing_votes = await conn.fetch(
                    "SELECT emoji FROM votes WHERE poll_id=$1 AND user_id=$2",
                    self.poll_id, interaction.user.id
                )
                
                existing_emojis = [v["emoji"] for v in existing_votes]
                
                if emoji in existing_emojis:
                    await conn.execute(
                        "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2 AND emoji=$3",
                        self.poll_id, interaction.user.id, emoji
                    )
                    await interaction.response.send_message("✅ Vote annulé", ephemeral=True)
                    logger.info(f"Vote annulé: user={interaction.user.id}, poll={self.poll_id}, emoji={emoji}")
                else:
                    if not self.allow_multiple:
                        await conn.execute(
                            "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                            self.poll_id, interaction.user.id
                        )
                    
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
        
        except Exception as e:
            logger.error(f"❌ Erreur lors du vote: {e}")
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
        for member_id, username, current_vote in members_data:
            vote_text = f" → {current_vote}" if current_vote else " → ⏸️ Pas de vote"
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
            await interaction.response.send_modal(
                EditVoteSingleModal(self.poll_id, self.poll_data, member_data[0])
            )
    
    async def cancel_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("❌ Opération annulée", ephemeral=True)


class EditVoteSingleModal(Modal, title="✏️ Modifier le vote"):
    """Modal pour modifier le vote d'un seul membre"""
    
    def __init__(self, poll_id: int, poll_data: dict, member_data: tuple):
        super().__init__()
        self.poll_id = poll_id
        self.poll_data = poll_data
        self.member_id, self.member_name, self.current_vote = member_data
        
        options = []
        if poll_data["is_presence_poll"]:
            options = [
                ("✅ Présent", "✅"),
                ("⏳ En attente", "⏳"),
                ("❌ Absent", "❌"),
                ("🗑️ Supprimer le vote", "DELETE")
            ]
        else:
            for i, opt in enumerate(poll_data["options"]):
                options.append((opt, Config.EMOJIS[i]))
            options.append(("🗑️ Supprimer le vote", "DELETE"))
        
        options_str = "\n".join([f"• {label}" for label, _ in options])
        
        self.vote_input = TextInput(
            label=f"Nouveau vote pour {self.member_name[:20]}",
            placeholder=f"Actuel: {self.current_vote or 'Aucun'}\n\nOptions:\n{options_str}",
            required=True,
            max_length=100
        )
        self.add_item(self.vote_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_vote = self.vote_input.value.strip()
            
            async with database.db.acquire() as conn:
                await conn.execute(
                    "DELETE FROM votes WHERE poll_id=$1 AND user_id=$2",
                    self.poll_id, self.member_id
                )
                
                if new_vote and new_vote.lower() != "supprimer" and new_vote != "DELETE":
                    emoji = self._get_emoji_from_vote(new_vote)
                    if emoji:
                        await conn.execute(
                            "INSERT INTO votes (poll_id, user_id, emoji) VALUES ($1, $2, $3)",
                            self.poll_id, self.member_id, emoji
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
    
    def _get_emoji_from_vote(self, vote_text: str) -> str:
        """Retourne l'emoji correspondant au vote"""
        if self.poll_data["is_presence_poll"]:
            for label, emoji in [("présent", "✅"), ("present", "✅"), ("✅", "✅"),
                               ("en attente", "⏳"), ("⏳", "⏳"),
                               ("absent", "❌"), ("❌", "❌")]:
                if label in vote_text.lower():
                    return emoji
        else:
            for i, opt in enumerate(self.poll_data["options"]):
                if opt.lower() in vote_text.lower():
                    return Config.EMOJIS[i]
        return None