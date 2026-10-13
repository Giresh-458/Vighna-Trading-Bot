import asyncio
import os
from telethon import TelegramClient
from dotenv import load_dotenv
from app.commodity_signal_parser import CommoditySignalParser

load_dotenv()
api_id = os.getenv('TELEGRAM_API_ID')
api_hash = os.getenv('TELEGRAM_API_HASH')
channel_id = os.getenv('COMMODITY_CHANNEL_ID')

client = TelegramClient('telegram_session', api_id, api_hash)

async def main():
    await client.start()
    print(f'Fetching messages from channel: {channel_id}')
    try:
        messages = await client.get_messages(int(channel_id), limit=5)
        for m in messages:
            if m.text:
                print('-------------------------')
                print('MESSAGE:')
                print(m.text)
                print('PARSED:')
                res = CommoditySignalParser.parse_all(m.text)
                if res:
                    for r in res:
                        print(r.__dict__)
                else:
                    print('FAILED TO PARSE')
    except Exception as e:
        print('Error:', e)

asyncio.run(main())
