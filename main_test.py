# main_test.py
import uvicorn
from fastapi import FastAPI
from dotenv import load_dotenv

# Load your .env file
load_dotenv()

# Import the router from your test file
from web_ui_tester import router as test_router

# Create the app and attach the UI/Websocket routes
app = FastAPI(title="Mithra AI Local Tester")
app.include_router(test_router)

if __name__ == "__main__":
    print("🚀 Starting Local Test Server...")
    print("🌐 Open your browser to: http://localhost:8000/test-ui")
    uvicorn.run(app, host="0.0.0.0", port=8000)