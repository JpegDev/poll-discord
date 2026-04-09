import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from collections import defaultdict

import sys
sys.path.insert(0, '/Users/jean-philippearnaudin/Documents/poll-discord')
from utils.config import Config
from utils.modals import DateModal
from utils.poll_utils import _build_poll_content
from utils.views import PollView, PresencePollView


class TestDateParsing:
    """Tests pour le parsing des dates dans DateModal"""
    
    def test_parse_date_without_time(self):
        """Test parsing date sans heure"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        result = modal._parse_date("25/12/2024")
        assert result.day == 25
        assert result.month == 12
        assert result.year == 2024
        assert result.hour == 0
        assert result.minute == 0
        assert result.tzinfo == Config.TZ
    
    def test_parse_date_with_time(self):
        """Test parsing date avec heure"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        result = modal._parse_date("25/12/2024-20:30")
        assert result.day == 25
        assert result.month == 12
        assert result.year == 2024
        assert result.hour == 20
        assert result.minute == 30
        assert result.tzinfo == Config.TZ
    
    def test_parse_date_invalid_format(self):
        """Test format de date invalide"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        with pytest.raises(ValueError):
            modal._parse_date("25-12-2024")


class TestDateValidation:
    """Tests pour la validation des dates"""
    
    def test_date_in_past(self):
        """Test qu'une date dans le passé est rejetée"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        past_date = datetime.now(Config.TZ) - timedelta(days=1)
        result = modal._validate_dates(past_date)
        assert result is not None
        assert "passé" in result
    
    def test_date_too_far_ahead(self):
        """Test qu'une date trop lointaine est rejetée"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        far_future = datetime.now(Config.TZ) + timedelta(days=800)
        result = modal._validate_dates(far_future)
        assert result is not None
        assert "plus de" in result
    
    def test_valid_date(self):
        """Test qu'une date valide est acceptée"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        future_date = datetime.now(Config.TZ) + timedelta(days=30)
        result = modal._validate_dates(future_date)
        assert result is None
    
    def test_max_date_before_now(self):
        """Test que max_date dans le passé est rejectée"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        event_date = datetime.now(Config.TZ) + timedelta(days=30)
        max_date = datetime.now(Config.TZ) - timedelta(days=1)
        result = modal._validate_dates(event_date, max_date)
        assert result is not None
        assert "passé" in result
    
    def test_max_date_after_event(self):
        """Test que max_date après event_date est rejectée"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        event_date = datetime.now(Config.TZ) + timedelta(days=30)
        max_date = datetime.now(Config.TZ) + timedelta(days=35)
        result = modal._validate_dates(event_date, max_date)
        assert result is not None
        assert "avant" in result
    
    def test_valid_dates(self):
        """Test que des dates valides sont acceptées"""
        from utils.modals import DateModal
        modal = DateModal("test", [], False)
        event_date = datetime.now(Config.TZ) + timedelta(days=30)
        max_date = datetime.now(Config.TZ) + timedelta(days=25)
        result = modal._validate_dates(event_date, max_date)
        assert result is None


class TestPollContentBuilding:
    """Tests pour la construction du contenu des sondages"""
    
    def _create_mock_guild_and_channel(self):
        """Crée des mocks pour guild et channel"""
        guild = MagicMock()
        guild.members = []
        
        channel = MagicMock()
        channel.permissions_for.return_value.read_messages = True
        
        return guild, channel
    
    def test_build_poll_content_classic(self):
        """Test construction contenu sondage classique"""
        from utils.poll_utils import _build_poll_content
        
        poll = {
            "id": 1,
            "question": "Test question?",
            "options": ["Oui", "Non"],
            "is_presence_poll": False,
            "allow_multiple": False,
            "event_date": datetime(2024, 12, 25, 20, 0, tzinfo=Config.TZ),
            "max_date": None
        }
        vote_counts = defaultdict(list, {"🇦": [1, 2], "🇧": [3]})
        user_votes = defaultdict(list)
        guild, channel = self._create_mock_guild_and_channel()
        
        result = _build_poll_content(poll, vote_counts, user_votes, guild, channel, [])
        
        assert "Test question?" in result
        assert "Oui" in result
        assert "Non" in result
        assert "2)" in result
        assert "1)" in result
    
    def test_build_poll_content_presence(self):
        """Test construction contenu sondage présence"""
        from utils.poll_utils import _build_poll_content
        
        poll = {
            "id": 2,
            "question": "Réunion",
            "options": [],
            "is_presence_poll": True,
            "allow_multiple": False,
            "event_date": datetime(2024, 12, 25, 20, 0, tzinfo=Config.TZ),
            "max_date": None
        }
        vote_counts = defaultdict(list, {
            "✅": [1, 2],
            "⏳": [3],
            "❌": [4]
        })
        user_votes = defaultdict(list)
        guild, channel = self._create_mock_guild_and_channel()
        
        result = _build_poll_content(poll, vote_counts, user_votes, guild, channel, [])
        
        assert "Réunion" in result
        assert "Présent" in result
        assert "En attente" in result
        assert "Absent" in result
    
    def test_build_poll_content_with_max_date(self):
        """Test construction avec date limite"""
        from utils.poll_utils import _build_poll_content
        
        poll = {
            "id": 3,
            "question": "Test",
            "options": ["A", "B"],
            "is_presence_poll": False,
            "allow_multiple": False,
            "event_date": datetime(2024, 12, 25, 20, 0, tzinfo=Config.TZ),
            "max_date": datetime(2024, 12, 20, 18, 0, tzinfo=Config.TZ)
        }
        vote_counts = defaultdict(list)
        user_votes = defaultdict(list)
        guild, channel = self._create_mock_guild_and_channel()
        
        result = _build_poll_content(poll, vote_counts, user_votes, guild, channel, [])
        
        assert "limite" in result.lower() or "20/12/2024" in result
    
    def test_build_poll_content_ended(self):
        """Test contenu quand le vote est terminé"""
        from utils.poll_utils import _build_poll_content
        
        poll = {
            "id": 4,
            "question": "Test",
            "options": ["A", "B"],
            "is_presence_poll": False,
            "allow_multiple": False,
            "event_date": datetime(2024, 12, 25, 20, 0, tzinfo=Config.TZ),
            "max_date": datetime(2024, 12, 1, 0, 0, tzinfo=Config.TZ)
        }
        vote_counts = defaultdict(list)
        user_votes = defaultdict(list)
        guild, channel = self._create_mock_guild_and_channel()
        
        with patch('bot.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2024, 12, 20, 12, 0, tzinfo=Config.TZ)
            result = _build_poll_content(poll, vote_counts, user_votes, guild, channel, [])
        
        assert "terminé" in result.lower() or "🔒" in result


class TestConfig:
    """Tests pour la configuration"""
    
    def test_emoji_count(self):
        """Test qu'il y a suffisamment d'emojis"""
        from utils.config import Config
        assert len(Config.EMOJIS) >= 20
    
    def test_max_options_limit(self):
        """Test que MAX_OPTIONS est défini"""
        from utils.config import Config
        assert Config.MAX_OPTIONS == 20
    
    def test_reminder_intervals(self):
        """Test des intervalles de rappel"""
        from utils.config import Config
        assert Config.REMINDER_J_MINUS_2_MIN == 47
        assert Config.REMINDER_J_MINUS_2_MAX == 49
        assert Config.REMINDER_J_MINUS_1_MIN == 23
        assert Config.REMINDER_J_MINUS_1_MAX == 25


class TestReminderLogic:
    """Tests pour la logique des rappels"""
    
    def test_j_minus_2_window(self):
        """Test que J-2 est dans la bonne fenêtre"""
        from utils.config import Config
        from datetime import timedelta
        
        now = datetime.now(Config.TZ)
        deadline = now + timedelta(hours=48)
        time_until = deadline - now
        
        assert timedelta(hours=Config.REMINDER_J_MINUS_2_MIN) <= time_until <= timedelta(hours=Config.REMINDER_J_MINUS_2_MAX)
    
    def test_j_minus_1_window(self):
        """Test que J-1 est dans la bonne fenêtre"""
        from utils.config import Config
        from datetime import timedelta
        
        now = datetime.now(Config.TZ)
        deadline = now + timedelta(hours=24)
        time_until = deadline - now
        
        assert timedelta(hours=Config.REMINDER_J_MINUS_1_MIN) <= time_until <= timedelta(hours=Config.REMINDER_J_MINUS_1_MAX)


class TestPollView:
    """Tests pour les vues de sondage"""
    
    def test_poll_view_max_options(self):
        """Test que PollView limite les options"""
        from utils.views import PollView, PresencePollView
        
        options = [f"Option {i}" for i in range(25)]
        view = PollView(1, options, False)
        
        assert len(view.children) <= Config.MAX_OPTIONS + 1 + 1
    
    def test_poll_view_creates_buttons(self):
        """Test que PollView crée les boutons"""
        from utils.views import PollView
        
        options = ["Oui", "Non", "Peut-être"]
        view = PollView(1, options, False)
        
        assert len(view.children) == 4
    
    def test_presence_poll_view(self):
        """Test que PresencePollView crée les boutons"""
        from utils.views import PresencePollView
        
        view = PresencePollView(1)
        
        assert len(view.children) == 4
    
    def test_poll_view_allow_multiple(self):
        """Test que PollView gère allow_multiple"""
        from utils.views import PollView
        
        view_single = PollView(1, ["A", "B"], False)
        view_multiple = PollView(1, ["A", "B"], True)
        
        assert view_single.allow_multiple == False
        assert view_multiple.allow_multiple == True
    
    def test_poll_view_show_edit_false(self):
        """Test que PollView sans bouton edit"""
        from utils.views import PollView
        
        view = PollView(1, ["A", "B"], False, show_edit=False)
        
        edit_buttons = [c for c in view.children if hasattr(c, 'custom_id') and 'edit' in str(c.custom_id)]
        assert len(edit_buttons) == 0
    
    def test_presence_poll_show_edit_true(self):
        """Test que PresencePollView avec show_edit=True a le bouton"""
        from utils.views import PresencePollView
        
        view = PresencePollView(1, show_edit=True)
        
        edit_buttons = [c for c in view.children if hasattr(c, 'custom_id') and 'edit' in str(c.custom_id)]
        assert len(edit_buttons) == 1


class TestEditorConfig:
    """Tests pour la configuration éditeur"""
    
    def test_editor_role_id_none(self):
        """Test que EDITOR_ROLE_ID peut être None"""
        from utils.config import Config
        assert Config.EDITOR_ROLE_ID is None or isinstance(Config.EDITOR_ROLE_ID, int)
    
    def test_is_editor_false_when_no_role(self):
        """Test is_editor retourne False si pas de rôle configuré"""
        from utils.config import Config, is_editor
        
        mock_interaction = MagicMock()
        mock_interaction.user.roles = []
        
        if Config.EDITOR_ROLE_ID is None:
            assert is_editor(mock_interaction) == False


class TestEventCreation:
    """Tests pour la création des événements Discord"""
    
    def test_create_scheduled_event_signature(self):
        """Test que create_scheduled_event a les bons paramètres"""
        from utils.events import create_scheduled_event
        import inspect
        
        sig = inspect.signature(create_scheduled_event)
        params = list(sig.parameters.keys())
        
        assert "guild" in params
        assert "poll" in params


class TestPollContentWithID:
    """Tests pour l'affichage de l'ID dans le contenu"""
    
    def _create_mock_guild_and_channel(self):
        guild = MagicMock()
        guild.members = []
        channel = MagicMock()
        channel.permissions_for.return_value.read_messages = True
        return guild, channel
    
    def test_poll_content_includes_id(self):
        """Test que l'ID du poll est affiché"""
        from utils.poll_utils import _build_poll_content
        
        poll = {
            "id": 42,
            "question": "Test",
            "options": ["A", "B"],
            "is_presence_poll": False,
            "allow_multiple": False,
            "event_date": datetime(2024, 12, 25, 20, 0, tzinfo=Config.TZ),
            "max_date": None
        }
        vote_counts = defaultdict(list)
        user_votes = defaultdict(list)
        guild, channel = self._create_mock_guild_and_channel()
        
        result = _build_poll_content(poll, vote_counts, user_votes, guild, channel, [])
        
        assert "ID: 42" in result


class TestEditVoteModal:
    """Tests pour la modal de modification de vote"""
    
    def test_modal_creation_presence(self):
        """Test création modal pour vote présence"""
        from utils.views import EditVoteSingleModal
        
        poll_data = {
            "is_presence_poll": True,
            "options": []
        }
        member_data = (123, "User", "✅")
        
        modal = EditVoteSingleModal(1, poll_data, member_data)
        
        select_item = modal.children[0]
        assert len(select_item.options) == 4
    
    def test_modal_creation_classic(self):
        """Test création modal pour vote classique"""
        from utils.views import EditVoteSingleModal
        
        poll_data = {
            "is_presence_poll": False,
            "options": ["Oui", "Non"]
        }
        member_data = (123, "User", "🇦")
        
        modal = EditVoteSingleModal(1, poll_data, member_data)
        
        select_item = modal.children[0]
        assert len(select_item.options) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])