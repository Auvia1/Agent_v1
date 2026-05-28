from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
from livekit import api

async def delete_rooms():
    api_client = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET"),
    )
    rooms_to_delete = [
        "+918309833107_9Rxkit8hVrAw",  # RM_5xer83wXpATG
    ]
    try:
        for room in rooms_to_delete:
            try:
                await api_client.room.delete_room(api.DeleteRoomRequest(room=room))
                print(f"✅ Deleted: {room}")
            except Exception as e:
                print(f"❌ Failed {room}: {e}")
    finally:
        await api_client.aclose()

asyncio.run(delete_rooms())