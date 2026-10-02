from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateColumn

from .config import DATABASE_URL
from .models import Base

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionMaker = async_sessionmaker(engine, expire_on_commit=False)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _add_missing_columns(conn) -> list[str]:
    """ترحيل خفيف: يضيف الأعمدة الجديدة للجداول الموجودة (ALTER TABLE ADD COLUMN).
    يكفي لإضافة أعمدة فقط؛ لأي تغيير أكبر استخدم Alembic."""
    added = []
    insp = inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                ddl = str(CreateColumn(col).compile(dialect=conn.dialect))
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                added.append(f"{table.name}.{col.name}")
    return added


async def init_db() -> list[str]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        return await conn.run_sync(_add_missing_columns)
