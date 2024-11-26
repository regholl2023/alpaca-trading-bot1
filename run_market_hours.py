import asyncio
import datetime
import pytz
from trading import TradingExecutor
from fetch import fetch_historical_data, get_latest_data, is_market_open
from strategy import TradingStrategy
from telegram_bot import TelegramBot
from alpaca.trading.client import TradingClient
import os
from dotenv import load_dotenv
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def is_market_hours():
    """Check if it's currently market hours (9:30 AM - 4:00 PM Eastern, Monday-Friday)"""
    et_tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(et_tz)
    
    # Check if it's a weekday
    if now.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    
    # Market hours are 9:30 AM - 4:00 PM Eastern
    market_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_start <= now <= market_end

async def run_bot():
    """Main function to run the trading bot"""
    load_dotenv()
    
    # Initialize clients
    trading_client = TradingClient(
        os.getenv('ALPACA_API_KEY'),
        os.getenv('ALPACA_SECRET_KEY')
    )
    
    # Initialize bot components
    symbol = "SPY"  # or your preferred symbol
    trading_executor = TradingExecutor(trading_client, symbol)
    telegram_bot = TelegramBot(trading_client)
    
    # Start the Telegram bot in the background
    asyncio.create_task(telegram_bot.start())
    
    logger.info("Bot started, waiting for market hours...")
    
    while True:
        try:
            if is_market_hours():
                logger.info("Market is open, running trading logic...")
                # Your trading logic here
                # You might want to add your specific trading implementation
                await asyncio.sleep(300)  # Wait 5 minutes between iterations
            else:
                logger.info("Outside market hours, waiting...")
                # If outside market hours, check every 5 minutes
                await asyncio.sleep(300)
                
        except Exception as e:
            logger.error(f"Error in main loop: {str(e)}")
            await asyncio.sleep(60)  # Wait a minute before retrying

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
