import asyncio
import sys
import time
from datetime import datetime, timezone
from telethon import TelegramClient, events
from app.config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_NAME, TELEGRAM_CHANNEL_ID

async def main():
    print("====================================================")
    print("TELEGRAM LIVE MESSAGE DIAGNOSTIC")
    print("====================================================\n")

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        print("FAIL: TELEGRAM_API_ID or TELEGRAM_API_HASH is missing in .env")
        sys.exit(1)

    if not TELEGRAM_CHANNEL_ID:
        print("FAIL: TELEGRAM_CHANNEL_ID is missing in .env")
        print("Please run 'python -m app.setup_telegram' first to find your channel ID.")
        sys.exit(1)

    print("TELEGRAM CONNECTION: ATTEMPTING...")
    client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("ACCOUNT AUTHENTICATION: FAIL")
            print("Session not authorized. Please run 'python -m app.setup_telegram' first.")
            return

        print("TELEGRAM CONNECTION: PASS")
        print("ACCOUNT AUTHENTICATION: PASS")

        # Verify Channel Access
        try:
            entity = await client.get_entity(TELEGRAM_CHANNEL_ID)
            print(f"PRIVATE CHANNEL ACCESS: PASS")
            print(f"CHANNEL ID: {TELEGRAM_CHANNEL_ID}")
            print(f"CHANNEL TITLE: {getattr(entity, 'title', 'Unknown')}")
        except ValueError:
            print("PRIVATE CHANNEL ACCESS: FAIL")
            print(f"Could not resolve CHANNEL_ID: {TELEGRAM_CHANNEL_ID}. Make sure the ID is correct and your account is a member.")
            return
        except Exception as e:
            print("PRIVATE CHANNEL ACCESS: FAIL")
            print(f"Error accessing channel: {e}")
            return

        print("\nWaiting for a new message in the channel...")
        print("Send a test message in the channel now (or wait for one to arrive).")
        print("(Press Ctrl+C to stop waiting)")

        # Create an event to signal when a message is received
        message_received = asyncio.Event()

        @client.on(events.NewMessage(chats=TELEGRAM_CHANNEL_ID))
        async def handler(event):
            receive_timestamp_ns = time.time_ns()
            receive_dt = datetime.now(timezone.utc)
            
            # Telegram message timestamp (usually UTC)
            msg_dt = event.message.date
            
            # Calculate latency in ms
            latency_ms = (receive_dt - msg_dt).total_seconds() * 1000
            
            print("\n----------------------------------------------------")
            print("NEW MESSAGE RECEPTION: PASS")
            print(f"Message ID: {event.message.id}")
            print(f"Sent at (UTC): {msg_dt}")
            print(f"Received at (UTC): {receive_dt}")
            print(f"MESSAGE LATENCY: {latency_ms:.2f} ms")
            print("Content:")
            print(event.message.text)
            print("----------------------------------------------------\n")
            
            message_received.set()

        # Keep running until one message is received
        await message_received.wait()
        
        print("Diagnostic completed successfully.")

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
    finally:
        await client.disconnect()

if __name__ == '__main__':
    # Fix for Windows asyncio loop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDiagnostic stopped by user.")
