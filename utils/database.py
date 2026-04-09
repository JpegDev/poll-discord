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
    """Initialise les tables de la base de données avec migration"""
    try:
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
                    allow_multiple BOOLEAN DEFAULT FALSE,
                    event_id BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            
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
                
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS votes_new (
                        poll_id INTEGER REFERENCES polls(id) ON DELETE CASCADE,
                        user_id BIGINT,
                        emoji TEXT,
                        PRIMARY KEY (poll_id, user_id, emoji)
                    );
                """)
                
                await conn.execute("""
                    INSERT INTO votes_new (poll_id, user_id, emoji)
                    SELECT poll_id, user_id, emoji FROM votes
                    ON CONFLICT DO NOTHING;
                """)
                
                await conn.execute("DROP TABLE votes;")
                await conn.execute("ALTER TABLE votes_new RENAME TO votes;")
                
                logger.info("✅ Migration de la table votes terminée")
            else:
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

                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                  WHERE table_name='polls' AND column_name='event_id') THEN
                        ALTER TABLE polls ADD COLUMN event_id BIGINT;
                    END IF;
                END $$;
            """)
            
        logger.info("✅ Base de données initialisée")
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'initialisation de la DB: {e}")
        raise