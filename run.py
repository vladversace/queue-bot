import asyncio
import threading
from bot import main as bot_main
from dashboard import app
import os

def run_dashboard():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Start dashboard in separate thread
    dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
    dashboard_thread.start()
    
    # Run bot in main thread
    asyncio.run(bot_main())
