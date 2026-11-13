"""Tests de non-régression des bugs relevés à l'audit (numéros #n = rapport d'audit)."""
import contextlib
import re
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock

import discord
import pytest

from utils import database
from utils.config import Config


def fake_pool(conn):
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool


def fake_conn(**kw):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=kw.get("fetch", []))
    conn.fetchrow = AsyncMock(return_value=kw.get("fetchrow"))
    conn.fetchval = AsyncMock(return_value=kw.get("fetchval"))
    conn.execute = AsyncMock()
    return conn


def http_error(cls, status):
    return cls(Mock(status=status, reason="x"), "boom")


# --- #5 : une erreur transitoire ne doit pas passer pour "événement supprimé" ---

async def test_check_event_exists_not_found_is_false():
    from utils.events import check_event_exists
    guild = Mock(fetch_scheduled_event=AsyncMock(side_effect=http_error(discord.NotFound, 404)))
    assert await check_event_exists(guild, 1) is False


async def test_check_event_exists_server_error_is_not_false():
    from utils.events import check_event_exists
    guild = Mock(fetch_scheduled_event=AsyncMock(side_effect=http_error(discord.HTTPException, 500)))
    with contextlib.suppress(discord.HTTPException):  # lever l'erreur est acceptable
        assert await check_event_exists(guild, 1) is not False


# --- #4 : fermeture d'un sondage même si tout le monde a voté ---

async def test_close_poll_removes_buttons_when_everyone_voted(monkeypatch):
    from utils import reminders
    monkeypatch.setattr(reminders, "update_poll_display", AsyncMock())

    voter = Mock(id=10, bot=False)
    message = Mock(edit=AsyncMock(), jump_url="http://x")
    channel = Mock(fetch_message=AsyncMock(return_value=message))
    channel.guild.members = [voter]
    channel.permissions_for.return_value.read_messages = True

    monkeypatch.setattr(database, "db", fake_pool(fake_conn(fetch=[{"user_id": 10, "emoji": "🇦"}])))
    monkeypatch.setattr(database, "bot", Mock(get_channel=Mock(return_value=channel)))

    now = datetime.now(Config.TZ)
    poll = dict(id=1, channel_id=2, message_id=3, is_presence_poll=False, question="q",
                event_date=now + timedelta(days=3), max_date=now - timedelta(hours=1))
    await reminders.close_poll(poll)

    message.edit.assert_awaited_with(view=None)


# --- #9 : une seule réponse par interaction ---

async def test_handle_vote_error_does_not_respond_twice(monkeypatch):
    from utils import views
    monkeypatch.setattr(views, "update_poll_display", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(database, "db", fake_pool(fake_conn()))

    interaction = Mock(user=Mock(id=1), message=Mock())
    interaction.response.send_message = AsyncMock()
    await views.PollView(1, ["a", "b"], True, show_edit=False).handle_vote(interaction, "🇦")

    assert interaction.response.send_message.await_count <= 1


# --- #7 : Select Discord limité à 25 options ---

def test_member_select_caps_options_at_25():
    from utils.views import MemberSelectView
    members = [(i, f"user{i}", None) for i in range(30)]
    view = MemberSelectView(1, {}, members)
    assert len(view.children[0].options) <= 25


# --- #8 : created_at doit porter un fuseau horaire ---

async def test_polls_created_at_is_timezone_aware():
    conn = fake_conn(fetchval=False)
    database.db = fake_pool(conn)
    await database.init_db()
    sql = next(c.args[0] for c in conn.execute.await_args_list if "CREATE TABLE IF NOT EXISTS polls" in c.args[0])
    assert re.search(r"created_at\s+(TIMESTAMPTZ|TIMESTAMP WITH TIME ZONE)", sql)


# --- #6 : les rappels ne visent que les sondages dont l'événement n'est pas passé ---

async def test_non_voter_reminders_query_ignores_past_events(monkeypatch):
    from utils import reminders
    conn = fake_conn()
    monkeypatch.setattr(database, "db", fake_pool(conn))
    await reminders.send_non_voters_biweekly_reminders()
    assert "event_date" in conn.fetch.await_args_list[0].args[0]


# --- #10 : commandes réservées aux serveurs (user.roles n'existe pas en DM) ---

@pytest.mark.parametrize("name", ["poll_command", "delete_poll", "clean_events", "check_polls"])
def test_commands_are_guild_only(name):
    import bot
    assert getattr(bot, name).guild_only


# --- #2 : plage "pas de rappel" du vendredi 18h au dimanche 18h ---

@pytest.mark.parametrize("day,hour,quiet", [  # novembre 2026 : 12 = jeudi
    (12, 19, False),  # jeudi 19h
    (13, 17, False),  # vendredi 17h
    (13, 18, True),   # vendredi 18h
    (13, 19, True),   # vendredi 19h : l'heure d'envoi du scheduler quotidien
    (14, 12, True),   # samedi
    (15, 17, True),   # dimanche 17h
    (15, 18, False),  # dimanche 18h
    (16, 9, False),   # lundi
])
def test_is_quiet_time(day, hour, quiet):
    from utils.config import is_quiet_time
    assert is_quiet_time(datetime(2026, 11, day, hour, tzinfo=Config.TZ)) is quiet


# --- #1 : on_ready (rappelé à chaque reconnexion) ne doit plus rien initialiser ---

async def test_on_ready_does_not_reinitialise(monkeypatch):
    import bot
    get_db = AsyncMock()
    monkeypatch.setattr(database, "get_db", get_db)
    await bot.on_ready()
    await bot.on_ready()
    get_db.assert_not_awaited()


# --- #4 : on ne vote plus après la date limite ---

async def test_handle_vote_rejected_after_deadline(monkeypatch):
    from utils import views
    conn = fake_conn(fetchval=datetime.now(Config.TZ) - timedelta(minutes=1))
    monkeypatch.setattr(database, "db", fake_pool(conn))

    interaction = Mock(user=Mock(id=1))
    interaction.response.send_message = AsyncMock()
    await views.PollView(1, ["a", "b"], True, show_edit=False).handle_vote(interaction, "🇦")

    assert "terminé" in interaction.response.send_message.await_args.args[0]
    conn.execute.assert_not_awaited()


# --- Dates : asyncpg renvoie les timestamptz en UTC, l'affichage doit être en heure de Paris ---

def test_fmt_dt_converts_utc_to_paris():
    from datetime import timezone
    from utils.config import fmt_dt
    # 25/12 02:00 Paris (hiver, UTC+1) == 25/12 01:00 UTC ; avant : minuit Paris s'affichait "24/12 à 23:00"
    assert fmt_dt(datetime(2024, 12, 25, 1, 0, tzinfo=timezone.utc)) == "25/12/2024 à 02:00"
    assert fmt_dt(datetime(2024, 12, 24, 23, 0, tzinfo=timezone.utc)) == "25/12/2024 à 00:00"


def test_date_only_stays_same_day_in_utc():
    from datetime import timezone
    from utils.modals import DateModal
    for d in ("25/12/2024", "25/07/2024"):  # hiver et été
        utc = DateModal("q", [], False)._parse_date(d).astimezone(timezone.utc)
        assert utc.day == 25
