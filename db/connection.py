# #db/connection.py
# import os
# import asyncpg
# from loguru import logger
# from db.queries import cleanup_expired_pending_appointments
 
# # ✅ Initialize the singleton variable as None
# _pool = None  
 
# async def get_db_pool():
#     global _pool
    
#     # ✅ If the pool already exists, return it immediately without recreating
#     if _pool is not None:
#         return _pool  
        
#     try:
#         # ✅ Create the pool with the PgBouncer fix (statement_cache_size=0)
#         _pool = await asyncpg.create_pool(
#             os.getenv("DATABASE_URL"),
#             statement_cache_size=0
#         )
#         logger.info("✅ Database pool created successfully.")
        
#         # ✅ Run the sweep ONLY once at startup
#         await cleanup_expired_pending_appointments(_pool)  
        
#         return _pool
        
#     except Exception as e:
#         logger.error(f"❌ Failed to connect to database: {e}")
#         raise


import os
import asyncpg
from loguru import logger
from db.queries import cleanup_expired_pending_appointments

_pool = None

async def get_db_pool():
    global _pool

    if _pool is not None:
        return _pool

    try:
        database_url = os.getenv("DATABASE_URL")

        _pool = await asyncpg.create_pool(
            dsn=database_url,
            statement_cache_size=0,   # good for poolers
            ssl="require",            # important for Supabase
            min_size=1,
            max_size=5,
            timeout=30,
            command_timeout=30
        )

        logger.info("✅ Database pool created successfully.")

        await cleanup_expired_pending_appointments(_pool)

        return _pool

    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        raise