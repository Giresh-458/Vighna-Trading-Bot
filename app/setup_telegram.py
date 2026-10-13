import asyncio
import sys
from telethon import TelegramClient
from app.config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_NAME

async def main():
    print("====================================================")
    print("TELEGRAM SETUP & DIAGNOSTIC")
    print("====================================================\n")

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        print("FAIL: TELEGRAM_API_ID or TELEGRAM_API_HASH is missing in .env")
        print("Please obtain these from https://my.telegram.org and add them to your .env file.")
        sys.exit(1)

    print("TELEGRAM CONNECTION: ATTEMPTING...")
    
    # Initialize the client. This will create a .session file in the current directory.
    client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        # Start the client. This will prompt for phone number and OTP in the console if not already authorized.
        await client.start()
        print("TELEGRAM CONNECTION: PASS")
        
        me = await client.get_me()
        print(f"ACCOUNT AUTHENTICATION: PASS (Logged in as {me.first_name} {me.last_name or ''})")

        print("\nFetching your recent channels and groups to help you find the CHANNEL_ID...")
        print("----------------------------------------------------")
        
        found_channels = False
        async for dialog in client.iter_dialogs():
            if dialog.is_channel or dialog.is_group:
                found_channels = True
                print(f"Name: {dialog.name[:40].ljust(40)} | ID: {dialog.id}")

        if not found_channels:
            print("No channels or groups found in your dialogs.")

        print("----------------------------------------------------")
        print("\nSETUP COMPLETE.")
        print("Next steps:")
        print("1. Find your target private channel in the list above.")
        print("2. Copy its ID (it usually starts with -100).")
        print("3. Paste it into your .env file as TELEGRAM_CHANNEL_ID=-100XXXXXXXXXX")
        print("4. Run 'python -m app.test_telegram' to verify live message reception.")

    except Exception as e:
        print(f"\nTELEGRAM CONNECTION: FAIL")
        print(f"Error details: {e}")
    finally:
        await client.disconnect()

if __name__ == '__main__':
    # Fix for Windows asyncio loop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
