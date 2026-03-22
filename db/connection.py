import os
import asyncpg
from loguru import logger
from db.queries import cleanup_expired_pending_appointments

async def get_db_pool():
    try:
        pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
        logger.info("✅ Database pool created successfully.")
        
        # 🔥 Run the 15-minute cleanup sweep immediately on boot
        await cleanup_expired_pending_appointments(pool)
        
        return pool
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        raise

# import os
# import ssl
# import asyncpg
# from loguru import logger
# from db.queries import cleanup_expired_pending_appointments

# async def get_db_pool():
#     try:
#         # Build DSN from individual env vars (matching your .env)
#         host     = os.getenv("DB_HOST")
#         port     = int(os.getenv("DB_PORT", 5432))
#         database = os.getenv("DB_NAME")
#         user     = os.getenv("DB_USER")
#         password = os.getenv("DB_PASSWORD")
#         use_ssl  = os.getenv("DB_SSL", "false").lower() == "true"

#         ssl_ctx = None
#         if use_ssl:
#             ssl_ctx = ssl.create_default_context()
#             # AWS RDS uses a CA-signed cert; if you haven't pinned the RDS CA bundle,
#             # this relaxes hostname verification while still encrypting the connection.
#             # To fully verify, download the RDS CA bundle and set ssl_ctx.load_verify_locations("rds-ca.pem")
#             ssl_ctx.check_hostname = False
#             ssl_ctx.verify_mode = ssl.CERT_NONE

#         pool = await asyncpg.create_pool(
#             host=host,
#             port=port,
#             database=database,
#             user=user,
#             password=password,
#             ssl=ssl_ctx,
#             min_size=2,
#             max_size=10,
#         )

#         logger.info("✅ Database pool created successfully.")

#         # Run the 15-minute cleanup sweep immediately on boot
#         await cleanup_expired_pending_appointments(pool)

#         return pool

#     except Exception as e:
#         logger.error(f"❌ Failed to connect to database: {e}")
#         raise