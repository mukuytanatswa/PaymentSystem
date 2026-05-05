"""
Run all migrations in sorted order against the configured DATABASE_URL.
Strips the +asyncpg driver suffix before connecting via asyncpg directly.

Usage:
    python scripts/run_migrations.py
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()


def _raw_dsn(database_url: str) -> str:
    return re.sub(r'\+asyncpg', '', database_url, count=1)


async def run_migrations() -> None:
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print('ERROR: DATABASE_URL is not set', file=sys.stderr)
        sys.exit(1)

    dsn = _raw_dsn(database_url)
    migrations_dir = Path(__file__).parent.parent / 'migrations'
    sql_files = sorted(migrations_dir.glob('*.sql'))

    if not sql_files:
        print('No migration files found.')
        return

    conn = await asyncpg.connect(dsn)
    try:
        for sql_file in sql_files:
            print(f'Running {sql_file.name}...', end=' ')
            sql = sql_file.read_text(encoding='utf-8')
            await conn.execute(sql)
            print('done')
    finally:
        await conn.close()

    print(f'\n{len(sql_files)} migration(s) completed successfully.')


if __name__ == '__main__':
    asyncio.run(run_migrations())
