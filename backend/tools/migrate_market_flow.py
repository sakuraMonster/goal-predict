import asyncio
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.database import engine


async def _add_team_style_tag(conn):
    dialect = engine.dialect.name
    if dialect == "postgresql":
        await conn.execute(text("ALTER TABLE teams ADD COLUMN IF NOT EXISTS style_tag VARCHAR(20)"))
        return

    if dialect == "sqlite":
        result = await conn.execute(text("PRAGMA table_info(teams)"))
        cols = {row[1] for row in result.all()}
        if "style_tag" not in cols:
            await conn.execute(text("ALTER TABLE teams ADD COLUMN style_tag VARCHAR(20)"))
        return

    try:
        await conn.execute(text("ALTER TABLE teams ADD COLUMN style_tag VARCHAR(20)"))
    except Exception as e:
        raise RuntimeError(
            f"Unknown SQL dialect '{dialect}' while adding teams.style_tag; "
            "attempted `ALTER TABLE teams ADD COLUMN style_tag VARCHAR(20)` but failed. "
            f"Original error: {type(e).__name__}: {e}"
        ) from e


async def _create_market_flow_tables(conn):
    dialect = engine.dialect.name

    if dialect == "sqlite":
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS jczq_play_odds_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                snapshot_time DATETIME NOT NULL,
                source VARCHAR(50) NOT NULL,
                had_home REAL,
                had_draw REAL,
                had_away REAL,
                hhad_line REAL,
                hhad_home REAL,
                hhad_draw REAL,
                hhad_away REAL,
                ttg_odds_json TEXT,
                crs_odds_json TEXT
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS market_flow_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL UNIQUE,
                odds_snapshot_id INTEGER,
                model_version VARCHAR(50) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT (datetime('now')),
                home_style_tag VARCHAR(20),
                away_style_tag VARCHAR(20),
                best_total_goals INTEGER,
                second_total_goals INTEGER,
                best_score VARCHAR(20),
                second_score VARCHAR(20),
                trace_json TEXT
            )
        """))
        return

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS jczq_play_odds_snapshots (
            id SERIAL PRIMARY KEY,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            snapshot_time TIMESTAMP NOT NULL,
            source VARCHAR(50) NOT NULL,
            had_home DOUBLE PRECISION,
            had_draw DOUBLE PRECISION,
            had_away DOUBLE PRECISION,
            hhad_line DOUBLE PRECISION,
            hhad_home DOUBLE PRECISION,
            hhad_draw DOUBLE PRECISION,
            hhad_away DOUBLE PRECISION,
            ttg_odds_json JSON,
            crs_odds_json JSON
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS market_flow_predictions (
            id SERIAL PRIMARY KEY,
            match_id INTEGER NOT NULL UNIQUE REFERENCES matches(id),
            odds_snapshot_id INTEGER REFERENCES jczq_play_odds_snapshots(id),
            model_version VARCHAR(50) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            home_style_tag VARCHAR(20),
            away_style_tag VARCHAR(20),
            best_total_goals INTEGER,
            second_total_goals INTEGER,
            best_score VARCHAR(20),
            second_score VARCHAR(20),
            trace_json JSON
        )
    """))


async def main():
    try:
        async with engine.begin() as conn:
            await _add_team_style_tag(conn)
            await _create_market_flow_tables(conn)
    except Exception as e:
        print(f"migrate_market_flow failed: {e}", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
