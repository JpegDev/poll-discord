import asyncpg
import logging
import os

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
db = None
bot = None


async def get_db():
    """Crée le pool de connexions à la base de données"""
    try:
        return await asyncpg.create_pool(DATABASE_URL)
    except Exception as e:
        logger.error(f"❌ Erreur de connexion à la base de données: {e}")
        raise


async def init_db():
    """Initialise les tables de la base de données (idempotent, une seule transaction)"""
    try:
        async with db.acquire() as conn, conn.transaction():
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
                    event_id BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

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

            # Colonnes ajoutées après la première version
            await conn.execute("""
                ALTER TABLE polls
                    ADD COLUMN IF NOT EXISTS event_date TIMESTAMP WITH TIME ZONE,
                    ADD COLUMN IF NOT EXISTS max_date TIMESTAMP WITH TIME ZONE,
                    ADD COLUMN IF NOT EXISTS is_presence_poll BOOLEAN DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS allow_multiple BOOLEAN DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS event_id BIGINT;
            """)

            # created_at était un TIMESTAMP sans fuseau (UTC côté Postgres) : on le rend explicite
            created_at_type = await conn.fetchval("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'polls' AND column_name = 'created_at'
            """)
            if created_at_type == "timestamp without time zone":
                await conn.execute("""
                    ALTER TABLE polls ALTER COLUMN created_at TYPE TIMESTAMPTZ
                    USING created_at AT TIME ZONE 'UTC'
                """)
                logger.info("🔄 polls.created_at migré en TIMESTAMPTZ")

        logger.info("✅ Base de données initialisée")
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'initialisation de la DB: {e}")
        raise
