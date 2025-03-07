import os
from datetime import datetime
import logging
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from strategy import TradingStrategy
from alpaca.trading.client import TradingClient
from visualization import create_strategy_plot, create_multi_symbol_plot
from config import TRADING_SYMBOLS, default_backtest_interval, PER_SYMBOL_CAPITAL_MULTIPLIER
from trading import TradingExecutor
from backtest import run_portfolio_backtest, create_portfolio_backtest_plot, create_portfolio_with_prices_plot
from backtest_individual import run_backtest, create_backtest_plot
from portfolio import get_portfolio_history, create_portfolio_plot
import pandas as pd
import pytz
from utils import get_api_symbol, get_display_symbol
import asyncio
import json

logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, trading_client: TradingClient, strategies: dict, symbols: list):
        self.trading_client = trading_client
        self.strategies = strategies  # Dict of symbol -> TradingStrategy
        self.symbols = symbols
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('CHAT_ID')
        
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")
        if not self.chat_id:
            raise ValueError("CHAT_ID not found in environment variables")
            
        # Initialize trading executors for each symbol
        self.executors = {symbol: TradingExecutor(trading_client, symbol) for symbol in symbols}
            
        # Initialize the application and bot
        self.application = Application.builder().token(self.bot_token).build()
        self._bot = None  # Will be initialized in start()
        self.setup_handlers(self.application)
            
    def get_best_params(self, symbol):
            """Get best parameters for a symbol from JSON file"""
            try:
                with open("best_params.json", "r") as f:
                    best_params_data = json.load(f)
                if symbol in best_params_data:
                    return best_params_data[symbol]['best_params']
                else:
                    return "Using default parameters"
            except FileNotFoundError:
                return "Using default parameters"

    def setup_handlers(self, application: Application):
        """Setup all command handlers"""
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("position", self.position_command))
        application.add_handler(CommandHandler("balance", self.balance_command))
        application.add_handler(CommandHandler("performance", self.performance_command))
        application.add_handler(CommandHandler("indicators", self.indicators_command))
        application.add_handler(CommandHandler("plot", self.plot_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("signals", self.signals_command))
        application.add_handler(CommandHandler("markets", self.markets_command))
        application.add_handler(CommandHandler("symbols", self.symbols_command))
        application.add_handler(CommandHandler("open", self.open_command))
        application.add_handler(CommandHandler("close", self.close_command))
        application.add_handler(CommandHandler("backtest", self.backtest_command))
        application.add_handler(CommandHandler("portfolio", self.portfolio_command))
        application.add_handler(CommandHandler("invest", self.invest_command))
        application.add_handler(CommandHandler("rank", self.rank_command))
        
        # Add callback query handler for inline buttons
        application.add_handler(CallbackQueryHandler(self.button_callback))

    @property
    def bot(self):
        """Lazy initialization of bot instance"""
        if self._bot is None:
            self._bot = Bot(token=self.bot_token)
        return self._bot

    async def start(self):
        """Start the Telegram bot"""
        try:
            # Initialize the application
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            
            # Send startup message
            await self.send_message("🤖 Trading Bot started successfully!")
            
            # Log startup
            logger.info(f"Starting trading bot for {', '.join(self.symbols)}")
            print(f"Starting trading bot for {', '.join(self.symbols)}...")
            print("Telegram bot initialized. Use /start to begin.")
            
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            raise

    async def stop(self):
        """Stop the Telegram bot"""
        try:
            if self._bot:
                await self._bot.close()
                self._bot = None
                
            if hasattr(self.application, 'updater'):
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")
            
    async def send_message(self, message: str):
        """Send message to Telegram"""
        try:
            # Split message into chunks of 4096 characters (Telegram's limit)
            chunks = [message[i:i+4096] for i in range(0, len(message), 4096)]
            for chunk in chunks:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=chunk,
                    parse_mode='HTML',  # Enable HTML formatting
                    disable_web_page_preview=True  # Disable link previews
                )
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    async def send_photo(self, photo_buffer):
        """Send photo to Telegram"""
        try:
            await self.bot.send_photo(
                chat_id=self.chat_id,
                photo=photo_buffer
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram photo: {e}")
            
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the bot and show available commands"""
        commands = """
🤖 Multi-Symbol Trading Bot Commands:

📊 Status Commands:
/status [symbol] - Get current trading status (all symbols if none specified)
/position [symbol] - View current position details
/balance - Check account balance
/performance - View today's performance
/rank - View performance ranking of all assets

📈 Analysis Commands:
/indicators [symbol] - View current indicator values
/plot [symbol] [days] - Generate strategy visualization
/signals - View latest signals for all symbols
/backtest [symbol] [days] - Run backtest simulation (default: all symbols, 5 days)
/backtest portfolio [days] - Run portfolio backtest (default: all symbols, 5 days)

💰 Trading Commands:
/open <symbol> <amount> - Open a position with specified amount
/close [symbol] - Close positions (all positions if no symbol specified)
/invest [symbol] <amount> - Invest specified amount in the specified symbol

⚙️ Management Commands:
/symbols - List all trading symbols
/markets - View market hours for all symbols
/help - Show this help message
/portfolio - Get portfolio history graph. Examples:
    • /portfolio (default: daily data for 1 month)
    • /portfolio 1H 1W (hourly data for 1 week)
    • /portfolio 15Min 1D (15-min data for 1 day)
    • /portfolio 1D 3M (daily data for 3 months)
        """
        symbols_list = "\n".join([f"• {symbol}" for symbol in self.symbols])
        await update.message.reply_text(f"Trading bot started\nMonitoring the following symbols:\n{symbols_list}\n\n{commands}")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get current status"""
        try:
            # Check if a specific symbol was requested
            symbol = context.args[0].upper() if context.args else None
            
            if symbol and symbol not in self.symbols:
                await update.message.reply_text(f"❌ Invalid symbol: {symbol}")
                return
                
            symbols_to_check = [symbol] if symbol else self.symbols
            has_data = False
            
            # Process symbols in chunks of 3
            for i in range(0, len(symbols_to_check), 3):
                chunk_messages = []
                chunk_symbols = symbols_to_check[i:i+3]
                
                for sym in chunk_symbols:
                    try:
                        analysis = self.strategies[sym].analyze()
                        if not analysis:
                            chunk_messages.append(f"No data available for {sym}")
                            continue
                            
                        has_data = True
                        position = "LONG" if self.strategies[sym].current_position == 1 else "SHORT" if self.strategies[sym].current_position == -1 else "NEUTRAL"
                        
                        # Get best parameters
                        params = self.get_best_params(sym)
                        params_message = f"\nParameters: {params}"

                        # Get position details if any
                        try:
                            pos = self.trading_client.get_open_position(get_api_symbol(sym))
                            pos_pnl = f"P&L: ${float(pos.unrealized_pl):.2f} ({float(pos.unrealized_plpc)*100:.2f}%)"
                        except:
                            pos_pnl = "No open position"
                        
                        chunk_messages.append(f"""
📊 {sym} ({TRADING_SYMBOLS[sym]['name']}) Status:
Position: {position}
Current Price: ${analysis['current_price']:.2f}
{pos_pnl}

Indicators:
• Daily Composite: {analysis['daily_composite']:.4f}
  - Upper: {analysis['daily_upper_limit']:.4f}
  - Lower: {analysis['daily_lower_limit']:.4f}
• Weekly Composite: {analysis['weekly_composite']:.4f}
  - Upper: {analysis['weekly_upper_limit']:.4f}
  - Lower: {analysis['weekly_lower_limit']:.4f}{params_message}

Price Changes:
• 5min: {analysis['price_change_5m']*100:.2f}%
• 1hr: {analysis['price_change_1h']*100:.2f}%""")
                    except Exception as e:
                        chunk_messages.append(f"Error analyzing {sym}: {str(e)}")
                
                # Send this chunk of messages
                if chunk_messages:
                    await update.message.reply_text("\n---\n".join(chunk_messages))
            
            if not has_data:
                await update.message.reply_text("❌ No data available for any symbol. The market may be closed or there might be connection issues.")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting status: {str(e)}")

    async def position_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get current position details"""
        try:
            # Check if a specific symbol was requested
            symbol = context.args[0].upper() if context.args else None
            
            if symbol and symbol not in self.symbols:
                await update.message.reply_text(f"❌ Invalid symbol: {symbol}")
                return
                
            symbols_to_check = [symbol] if symbol else self.symbols
            
            # Process symbols in chunks of 3
            for i in range(0, len(symbols_to_check), 3):
                chunk_messages = []
                chunk_symbols = symbols_to_check[i:i+3]
                
                for sym in chunk_symbols:
                    try:
                        position = self.trading_client.get_open_position(get_api_symbol(sym))
                        # Get account equity for exposure calculation
                        account = self.trading_client.get_account()
                        equity = float(account.equity)
                        market_value = float(position.market_value)
                        exposure_percentage = (market_value / equity) * 100
                        
                        message = f"""
📈 {sym} ({TRADING_SYMBOLS[sym]['name']}) Position Details:
Side: {position.side.upper()}
Quantity: {position.qty}
Entry Price: ${float(position.avg_entry_price):.2f}
Current Price: ${float(position.current_price):.2f}
Market Value: ${market_value:.2f}
Account Exposure: {exposure_percentage:.2f}%
Unrealized P&L: ${float(position.unrealized_pl):.2f} ({float(position.unrealized_plpc)*100:.2f}%)"""
                    except Exception as e:
                        logger.error(f"Error getting position for {sym} (API symbol: {get_api_symbol(sym)}): {str(e)}")
                        message = f"No open position for {sym} ({TRADING_SYMBOLS[sym]['name']})"
                    chunk_messages.append(message)
                
                # Send this chunk of messages
                if chunk_messages:
                    await update.message.reply_text("\n---\n".join(chunk_messages))
        
            # Add summary of all positions if not looking at a specific symbol
            if not symbol:
                try:
                    account = self.trading_client.get_account()
                    equity = float(account.equity)
                    total_market_value = 0
                    total_pnl = 0
                    positions_summary = []
                    
                    # Calculate totals and collect position details
                    for sym in self.symbols:
                        try:
                            position = self.trading_client.get_open_position(get_api_symbol(sym))
                            market_value = float(position.market_value)
                            total_market_value += market_value
                            total_pnl += float(position.unrealized_pl)
                            positions_summary.append({
                                'symbol': sym,
                                'market_value': market_value,
                                'side': position.side.upper(),
                                'qty': position.qty,
                                'pnl': float(position.unrealized_pl)
                            })
                        except Exception:
                            continue
                    
                    if total_market_value > 0:
                        total_exposure = (total_market_value / equity) * 100
                        
                        # Sort positions by market value
                        positions_summary.sort(key=lambda x: abs(x['market_value']), reverse=True)
                        
                        # Build positions text with weights
                        positions_text = []
                        for pos in positions_summary:
                            weight = (abs(pos['market_value']) / total_market_value) * 100
                            pnl_pct = (pos['pnl'] / abs(pos['market_value'])) * 100 if pos['market_value'] != 0 else 0
                            positions_text.append(
                                f"• {pos['symbol']}: {pos['side']} {pos['qty']} ({weight:.1f}% weight) | P&L: ${pos['pnl']:.2f} ({pnl_pct:.1f}%)"
                            )
                        
                        summary = f"""
💼 Portfolio Summary:
Total Position Value: ${total_market_value:.2f}
Total Account Exposure: {total_exposure:.2f}%
Total Unrealized P&L: ${total_pnl:.2f}

Open Positions:
{chr(10).join(positions_text)}"""
                        await update.message.reply_text(summary)
                except Exception as e:
                    logger.error(f"Error generating position summary: {str(e)}")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting position: {str(e)}")

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check account balance"""
        try:
            account = self.trading_client.get_account()
            message = f"""
💰 Account Balance:
Cash: ${float(account.cash):.2f}
Portfolio Value: ${float(account.portfolio_value):.2f}
Buying Power: ${float(account.buying_power):.2f}
Today's P&L: ${float(account.equity) - float(account.last_equity):.2f}
            """
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting balance: {str(e)}")

    async def performance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View today's performance"""
        try:
            account = self.trading_client.get_account()
            today_pl = float(account.equity) - float(account.last_equity)
            today_pl_pct = (today_pl / float(account.last_equity)) * 100
            
            message = f"""
📈 Today's Performance:
P&L: ${today_pl:.2f} ({today_pl_pct:.2f}%)
Starting Equity: ${float(account.last_equity):.2f}
Current Equity: ${float(account.equity):.2f}
            """
            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting performance: {str(e)}")

    async def indicators_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View current indicator values"""
        try:
            # Check if a specific symbol was requested
            symbol = context.args[0].upper() if context.args else None
            
            if symbol and symbol not in self.symbols:
                await update.message.reply_text(f"❌ Invalid symbol: {symbol}")
                return
                
            symbols_to_check = [symbol] if symbol else self.symbols
            has_data = False
            
            # Process symbols in chunks of 3
            for i in range(0, len(symbols_to_check), 3):
                chunk_messages = []
                chunk_symbols = symbols_to_check[i:i+3]
                
                for sym in chunk_symbols:
                    try:
                        analysis = self.strategies[sym].analyze()
                        if not analysis:
                            chunk_messages.append(f"No data available for {sym}")
                            continue
                        # Get best parameters
                        params = self.get_best_params(sym)
                        params_message = f"\nParameters: {params}"

                        has_data = True
                        message = f"""
📈 {sym} ({TRADING_SYMBOLS[sym]['name']}) Indicators:

Daily Composite: {analysis['daily_composite']:.4f}
• Upper Limit: {analysis['daily_upper_limit']:.4f}
• Lower Limit: {analysis['daily_lower_limit']:.4f}

Weekly Composite: {analysis['weekly_composite']:.4f}
• Upper Limit: {analysis['weekly_upper_limit']:.4f}
• Lower Limit: {analysis['weekly_lower_limit']:.4f}{params_message}

Price Changes:
• 5min: {analysis['price_change_5m']*100:.2f}%
• 1hr: {analysis['price_change_1h']*100:.2f}%"""
                        chunk_messages.append(message)
                    except Exception as e:
                        chunk_messages.append(f"Error analyzing {sym}: {str(e)}")
                
                # Send this chunk of messages
                if chunk_messages:
                    await update.message.reply_text("\n---\n".join(chunk_messages))
            
            if not has_data:
                await update.message.reply_text("❌ No data available for any symbol. The market may be closed or there might be connection issues.")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting indicators: {str(e)}")

    async def plot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Generate and send strategy visualization plots."""
        try:
            # Parse arguments
            args = context.args if context.args else []
            
            # Default values
            days = 5
            symbols_to_plot = self.symbols
            
            if len(args) >= 1:
                # First argument could be either symbol or days
                if args[0].upper() in self.symbols:
                    # First arg is a symbol
                    symbols_to_plot = [args[0].upper()]
                    # Check if second arg is days
                    if len(args) >= 2:
                        try:
                            days = int(args[1])
                        except ValueError:
                            await update.message.reply_text("❌ Days must be a number")
                            return
                else:
                    # First arg should be days
                    try:
                        days = int(args[0])
                    except ValueError:
                        await update.message.reply_text(f"❌ Invalid input: {args[0]} is neither a valid symbol nor a number\nAvailable symbols: {', '.join(self.symbols)}")
                        return
            
            if days <= 0 or days > default_backtest_interval:
                await update.message.reply_text(f"❌ Days must be between 1 and {default_backtest_interval}")
                return
            
            await update.message.reply_text(f"📊 Generating plots for the last {days} days...")
            
            # Generate and send plot for each symbol
            for symbol in symbols_to_plot:
                # Get best parameters
                params = self.get_best_params(symbol)

                try:
                    buf, stats = create_strategy_plot(symbol, days)
                    
                    stats_message = f"""
📈 {symbol} ({TRADING_SYMBOLS[symbol]['name']}) Statistics ({days} days):
• Parameters: {params}

• Trading Days: {stats['trading_days']}
• Price Change: {stats['price_change']:.2f}%
• Buy Signals: {stats['buy_signals']}
• Sell Signals: {stats['sell_signals']}
"""
                    
                    await update.message.reply_document(
                        document=buf,
                        filename=f"{symbol}_strategy_{days}d.png",
                        caption=stats_message
                    )
                except Exception as e:
                    logger.error(f"Error plotting {symbol}: {str(e)}")
                    await update.message.reply_text(f"❌ Could not generate plot for {symbol}: {str(e)}")
                    continue
                    
        except ValueError as e:
            await update.message.reply_text(f"❌ Invalid input: {str(e)}")
        except Exception as e:
            logger.error(f"Plot command error: {str(e)}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def signals_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View latest signals for all symbols"""
        try:
            # Check if a specific symbol was requested
            symbol = context.args[0].upper() if context.args else None
            
            if symbol and symbol not in self.symbols:
                await update.message.reply_text(f"❌ Invalid symbol: {symbol}")
                return
                
            symbols_to_check = [symbol] if symbol else self.symbols
            signal_messages = []
            has_data = False
            
            # Process symbols in chunks of 3
            for i in range(0, len(symbols_to_check), 3):
                chunk_messages = []
                chunk_symbols = symbols_to_check[i:i+3]
                
                for sym in chunk_symbols:
                    try:
                        analysis = self.strategies[sym].analyze()
                        if not analysis:
                            chunk_messages.append(f"No data available for {sym}")
                            continue
                            
                        has_data = True
                        # Get signal strength and direction
                        signal_strength = abs(analysis['daily_composite'])
                        strength_emoji = "🔥" if signal_strength > 0.8 else "💪" if signal_strength > 0.5 else "👍"
                        signal_direction = "BUY" if analysis['daily_composite'] > 0 else "SELL"
                        
                        # Format time since last signal with signal type
                        last_signal_info = "No signals generated yet"
                        if analysis.get('last_signal_time') is not None:
                            now = pd.Timestamp.now(tz=pytz.UTC)
                            last_time = analysis['last_signal_time']
                            time_diff = now - last_time
                            hours = int(time_diff.total_seconds() / 3600)
                            minutes = int((time_diff.total_seconds() % 3600) / 60)
                            # Get the signal type from the stored composite value
                            signal_type = "BUY" if analysis['daily_composite'] > 0 else "SELL"
                            last_signal_info = f"Last {signal_type} signal {strength_emoji}: {last_time.strftime('%Y-%m-%d %H:%M')} UTC ({hours}h {minutes}m ago)"
                        
                        # Classify signals
                        signal_direction = "BUY" if analysis['daily_composite'] > 0 else "SELL"
                        daily_signal = (
                            "STRONG BUY" if analysis['daily_composite'] > analysis['daily_upper_limit']
                            else "STRONG SELL" if analysis['daily_composite'] < analysis['daily_lower_limit']
                            else "WEAK " + signal_direction if signal_strength > 0.5
                            else "NEUTRAL"
                        )
                        
                        weekly_signal = (
                            "STRONG BUY" if analysis['weekly_composite'] > analysis['weekly_upper_limit']
                            else "STRONG SELL" if analysis['weekly_composite'] < analysis['weekly_lower_limit']
                            else "WEAK BUY" if analysis['weekly_composite'] > 0
                            else "WEAK SELL" if analysis['weekly_composite'] < 0
                            else "NEUTRAL"
                        )
                                                # Get best parameters
                        params = self.get_best_params(sym)
                        params_message = f"\nParameters: {params}"

                        message = f"""
📊 {sym} ({TRADING_SYMBOLS[sym]['name']}) Signals:
⏱ {last_signal_info}

Daily Signal: {daily_signal}
• Composite: {analysis['daily_composite']:.4f}
• Strength: {signal_strength:.2f} {strength_emoji}
• Upper Limit: {analysis['daily_upper_limit']:.4f}
• Lower Limit: {analysis['daily_lower_limit']:.4f}

Weekly Signal: {weekly_signal}
• Composite: {analysis['weekly_composite']:.4f}
• Upper Limit: {analysis['weekly_upper_limit']:.4f}
• Lower Limit: {analysis['weekly_lower_limit']:.4f}{params_message}

Price Changes:
• 5min: {analysis['price_change_5m']*100:.2f}%
• 1hr: {analysis['price_change_1h']*100:.2f}%"""
                        chunk_messages.append(message)
                    except Exception as e:
                        chunk_messages.append(f"Error analyzing {sym}: {str(e)}")
                
                if chunk_messages:
                    await update.message.reply_text("\n---\n".join(chunk_messages))
            
            if not has_data:
                await update.message.reply_text("❌ No signals available. The market may be closed or there might be connection issues.")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting signals: {str(e)}")

    async def markets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View market hours for all symbols"""
        try:
            market_info = []
            
            for symbol in self.symbols:
                config = TRADING_SYMBOLS[symbol]
                market_info.append(f"""
{symbol} ({config['name']}) ({config['market']}):
• Trading Hours: {config['market_hours']['start']} - {config['market_hours']['end']}
• Timezone: {config['market_hours']['timezone']}
                """)
            
            await update.message.reply_text("🕒 Market Hours:\n" + "\n".join(market_info))
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting market hours: {str(e)}")

    async def symbols_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all trading symbols"""
        try:
            symbols_info = []
            
            for symbol in self.symbols:
                config = TRADING_SYMBOLS[symbol]
                symbols_info.append(f"""
{symbol} ({config['name']}):
• Market: {config['market']}
• Interval: {config['interval']}
                """)
            
            await update.message.reply_text("📈 Trading Symbols:\n" + "\n".join(symbols_info))
        except Exception as e:
            await update.message.reply_text(f"❌ Error listing symbols: {str(e)}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        await self.start_command(update, context)

    async def open_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Open a new position with specified amount"""
        try:
            if not context.args or len(context.args) != 2:
                await update.message.reply_text(
                    "❌ Usage: /open <symbol> <amount>\n"
                    "Example: /open SPY 1000 (to open $1000 position in SPY)"
                )
                return
            
            symbol = context.args[0].upper()
            try:
                amount = float(context.args[1])
            except ValueError:
                await update.message.reply_text("❌ Amount must be a number")
                return
            
            if symbol not in self.symbols:
                await update.message.reply_text(f"❌ Invalid symbol: {symbol}")
                return
            
            if amount <= 0:
                await update.message.reply_text("❌ Amount must be positive")
                return
            
            # Get current price from strategy
            analysis = self.strategies[symbol].analyze()
            if not analysis:
                await update.message.reply_text(f"❌ Unable to get current price for {symbol}")
                return
            
            current_price = analysis['current_price']
            
            # Execute the trade using the appropriate executor
            await self.executors[symbol].open_position(amount, current_price, self.send_message)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error opening position: {str(e)}")

    async def close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Close positions"""
        try:
            # If no symbol specified, close all positions
            symbol = context.args[0].upper() if context.args else None
            
            if symbol and symbol not in self.symbols:
                await update.message.reply_text(f"❌ Invalid symbol: {symbol}")
                return
            
            symbols_to_close = [symbol] if symbol else self.symbols
            success_count = 0
            
            for sym in symbols_to_close:
                try:
                    if await self.executors[sym].close_position(self.send_message):
                        success_count += 1
                except Exception as e:
                    await update.message.reply_text(f"❌ Error closing {sym} position: {str(e)}")
            
            if success_count > 0:
                message = f"Successfully closed {success_count} position(s)"
                if symbol:
                    message += f" for {symbol}"
                await update.message.reply_text(message)
            elif not symbol:  # No positions were closed when trying to close all
                await update.message.reply_text("No open positions to close")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error closing positions: {str(e)}")

    async def backtest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run backtest simulation"""
        try:
            # Parse arguments
            args = context.args if context.args else []
            
            # Default values
            days = 5
            
            if not args:
                await update.message.reply_text(
                    "Usage:\n"
                    "/backtest [days] - Run backtest on all symbols\n"
                    "/backtest <symbol> [days] - Run backtest on specific symbol\n"
                    "/backtest portfolio [days] - Run portfolio backtest"
                )
                return
            
            # Handle portfolio backtest
            if args[0].lower() == 'portfolio':
                if len(args) >= 2:
                    try:
                        days = int(args[1])
                    except ValueError:
                        await update.message.reply_text("❌ Days must be a number")
                        return
                
                # Validate days
                if days <= 0 or days > default_backtest_interval:
                    await update.message.reply_text(f"❌ Days must be between 1 and {default_backtest_interval}, currently {days}")
                    return
                
                status_message = await update.message.reply_text(f"🔄 Starting portfolio backtest for the last {days} days...")
                
                try:
                    # Create async task for the backtest
                    async def run_backtest_task():
                        try:
                            # Create a closure to track progress
                            symbols_processed = 0
                            total_symbols = len(self.symbols)
                            loop = asyncio.get_running_loop()
                            
                            def progress_callback(symbol):
                                nonlocal symbols_processed
                                symbols_processed += 1
                                # Schedule the coroutine on the event loop
                                loop.call_soon_threadsafe(
                                    lambda: asyncio.create_task(
                                        self._update_backtest_progress(
                                            status_message,
                                            symbols_processed,
                                            total_symbols,
                                            symbol
                                        )
                                    )
                                )
                            
                            # Run portfolio backtest with progress updates
                            result = await loop.run_in_executor(
                                None,
                                lambda: run_portfolio_backtest(
                                    self.symbols, 
                                    days, 
                                    progress_callback=progress_callback
                                )
                            )
                            
                            # Calculate final allocations based on position values at the end
                            # This will be our definitive allocation used for both display and invest
                            individual_results = result['individual_results']
                            last_data_point = result['data'].iloc[-1]
                            
                            # Get position values for the crypto assets
                            symbol_position_values = {}
                            total_position_value = 0
                            
                            for symbol in self.symbols:
                                position_value_col = f'{symbol}_value'
                                if position_value_col in last_data_point:
                                    position_value = last_data_point[position_value_col]
                                    symbol_position_values[symbol] = position_value
                                    total_position_value += position_value
                            
                            # Calculate allocations as a percentage of total positions
                            # This is critical - this is the actual allocation used in the graph
                            allocations = {}
                            if total_position_value > 0:
                                for symbol, value in symbol_position_values.items():
                                    allocations[symbol] = value / total_position_value
                            
                            # Store this allocation in the result for future reference
                            result['allocations'] = allocations
                            
                            # Get crypto symbols
                            crypto_symbols = [s for s in self.symbols if TRADING_SYMBOLS[s]['market'] == 'CRYPTO']
                            
                            # Calculate metrics
                            metrics = result['metrics']
                            summary = (
                                f"📊 Portfolio Backtest Results ({days} days)\n\n"
                                f"Initial Capital: ${metrics['initial_capital']:,.2f}\n"
                                f"Final Value: ${metrics['final_value']:,.2f}\n"
                                f"Total Return: {metrics['total_return']:.2f}%\n"
                                f"Max Drawdown: {metrics['max_drawdown']:.2f}%\n"
                                f"Capital Multiplier: {PER_SYMBOL_CAPITAL_MULTIPLIER:.2f}\n"
                                f"💰 Trading Costs: ${result['metrics']['trading_costs']:.2f}\n"
                            )
                            
                            turnover_msg = (
                                f"\n🔄 Portfolio Turnover: {metrics['turnover']['turnover']:.1%}\n"
                                f"📊 Trades: {metrics['turnover']['total_trades']} (Buy: {metrics['turnover']['buy_trades']}, Sell: {metrics['turnover']['sell_trades']})\n"
                                f"💰 Total Buy Value: ${metrics['turnover']['total_buy_value']:,.2f}\n"
                                f"💰 Total Sell Value: ${metrics['turnover']['total_sell_value']:,.2f}\n"
                                f"📦 Avg Buy Size: ${metrics['turnover']['avg_buy_size']:,.2f}\n"
                                f"📦 Avg Sell Size: ${metrics['turnover']['avg_sell_size']:,.2f}\n\n"
                            )
                            summary += turnover_msg
                            
                            # Add returns and allocations for each asset
                            for symbol in self.symbols:
                                ret = metrics['symbol_returns'].get(symbol, 0)
                                # Use our calculated allocations to ensure consistency with the graph
                                alloc = allocations.get(symbol, 0) * 100
                                # Only include assets with non-zero allocations
                                if alloc > 0.01:  # Include anything above 0.01%
                                    summary += f"{symbol}: {ret:.2f}% (Allocation: {alloc:.1f}%)\n"
                            
                            # Add allocation info for crypto assets specifically
                            if crypto_symbols:
                                summary += "\nCrypto Assets Allocation:\n"
                                for symbol in crypto_symbols:
                                    alloc = allocations.get(symbol, 0) * 100
                                    if alloc > 0.01:  # Include anything above 0.01%
                                        summary += f"{symbol}: {alloc:.1f}%\n"
                            
                            # Edit status message with completion
                            await status_message.edit_text("✅ Portfolio backtest completed!")
                            
                            # Create inline keyboard for buying option
                            keyboard = [[
                                InlineKeyboardButton(
                                    "Buy this allocation", 
                                    callback_data=f"buy_backtest:portfolio:{days}"
                                )
                            ]]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            # Store the result so invest_command can access it later
                            self._last_portfolio_backtest = {
                                'result': result,
                                'days': days,
                                'allocations': allocations
                            }
                            
                            # Send summary message with buy button
                            await update.message.reply_text(summary, reply_markup=reply_markup)
                            
                            # Generate and send plots
                            plot_buffer = await loop.run_in_executor(
                                None,
                                lambda: create_portfolio_backtest_plot(result)
                            )
                            await update.message.reply_photo(plot_buffer)
                            
                            prices_plot_buffer = await loop.run_in_executor(
                                None,
                                lambda: create_portfolio_with_prices_plot(result)
                            )
                            await update.message.reply_photo(prices_plot_buffer)
                            
                        except Exception as e:
                            logger.error(f"Error in backtest task: {str(e)}")
                            await update.message.reply_text(f"❌ An error occurred: {str(e)}")
                    
                    await run_backtest_task()
                    
                except Exception as e:
                    logger.error(f"Error running backtest: {str(e)}")
                    await update.message.reply_text(f"❌ An error occurred while running backtest: {str(e)}")
                
                return
            
            # Handle regular backtest
            try:
                days = int(args[0])
                symbol = None
            except ValueError:
                symbol = args[0].upper()
                if len(args) >= 2:
                    try:
                        days = int(args[1])
                    except ValueError:
                        await update.message.reply_text("❌ Days must be a number")
                        return
            
            # Validate symbol if provided
            if symbol and symbol not in self.symbols:
                await update.message.reply_text(f"❌ Invalid symbol: {symbol}\nAvailable symbols: {', '.join(self.symbols)}")
                return
            
            # Validate days
            if days <= 0 or days > default_backtest_interval:
                await update.message.reply_text(f"❌ Days must be between 1 and {default_backtest_interval}, currently {days}")
                return
            
            symbols_to_test = [symbol] if symbol else self.symbols
            
            status_message = await update.message.reply_text(f"🔄 Starting backtest for the last {days} days...")
            
            # Run backtest for each symbol
            for sym in symbols_to_test:
                # Get best parameters
                params = self.get_best_params(sym)
                params_message = f"\nParameters: {params}"

                try:
                    # Run backtest simulation asynchronously
                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: run_backtest(sym, days)
                    )
                    
                    # Generate plot in executor
                    buf, stats = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: create_backtest_plot(result)
                    )
                    
                    # Create performance message
                    message = f"""
📊 {sym} ({TRADING_SYMBOLS[sym]['name']}) Backtest Results ({days} days):
• Total Return: {stats['total_return']:.2f}%
• Total Trades: {stats['total_trades']}
• Win Rate: {stats['win_rate']:.1f}%
• Max Drawdown: {stats['max_drawdown']:.2f}%
• Sharpe Ratio: {stats['sharpe_ratio']:.2f}
• 🔄 Portfolio Turnover: {stats.get('turnover', 0):.1%}
• 💰 Trading Costs: ${stats.get('trading_costs', 0):.2f}
{params_message}
                    """
                    
                    # Send plot and stats
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=buf,
                        caption=message,
                        parse_mode='HTML'
                    )
                    
                    # Update status for multiple symbols
                    if len(symbols_to_test) > 1:
                        await status_message.edit_text(f"✅ Completed {sym}, processing next symbol...")
                        
                except Exception as e:
                    error_msg = str(e)
                    if "Error running backtest for" in error_msg:
                        error_msg = error_msg.split(": ", 1)[1]  # Get the actual error message
                    await update.message.reply_text(f"❌ Could not run backtest for {sym}: {error_msg}")
            
            # Final status update
            if len(symbols_to_test) > 1:
                await status_message.edit_text("✅ All backtests completed!")
            else:
                await status_message.edit_text("✅ Backtest completed!")
                
        except ValueError as e:
            await update.message.reply_text(f"❌ Invalid input: {str(e)}")
        except Exception as e:
            logger.error(f"Backtest command error: {str(e)}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def _update_backtest_progress(self, message, processed, total, current_symbol):
        """Update the backtest progress message"""
        try:
            progress = (processed / total) * 100
            await message.edit_text(
                f"🔄 Running portfolio backtest...\n"
                f"Progress: {progress:.1f}%\n"
                f"Currently processing: {current_symbol}"
            )
        except Exception as e:
            logger.error(f"Error updating backtest progress: {e}")

    async def portfolio_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send portfolio history graph"""
        try:
            # Get command arguments
            args = context.args
            timeframe = '1D'  # default
            period = '1M'     # default
            
            if len(args) >= 1:
                timeframe = args[0]
            if len(args) >= 2:
                period = args[1]
                
            # Get portfolio history
            portfolio_history = get_portfolio_history(timeframe=timeframe, period=period)
            
            # Create plot
            plot_buffer = create_portfolio_plot(portfolio_history)
            
            # Send plot
            await update.message.reply_photo(
                photo=plot_buffer,
                caption=f'Portfolio History (Timeframe: {timeframe}, Period: {period})'
            )
            
        except Exception as e:
            logger.error(f"Error in portfolio_command: {str(e)}")
            await update.message.reply_text(f"Error getting portfolio history: {str(e)}")

    async def rank_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display performance ranking of all assets"""
        try:
            # Get performance data for all symbols
            rankings = {}
            performances = {}
            for symbol in self.symbols:
                executor = self.executors[symbol]
                analysis = self.strategies[symbol].analyze()
                if analysis and 'current_price' in analysis:
                    rank, performance = executor.calculate_performance_ranking(analysis['current_price'])
                    rankings[symbol] = rank
                    performances[symbol] = performance

            # Sort symbols by ranking (best to worst)
            sorted_rankings = sorted(rankings.items(), key=lambda x: x[1], reverse=True)

            # Format message
            message = "📊 Asset Performance Ranking:\n\n"
            for i, (symbol, rank) in enumerate(sorted_rankings, 1):
                perf = performances[symbol]
                message += f"{i}. {get_display_symbol(symbol)}: {perf:.1f}% (Percentile: {rank*100:.1f}%)\n"

            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error in /rank command: {e}")
            await update.message.reply_text("❌ Error generating performance ranking. Please try again later.")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()  # Answer the callback query to remove the loading state
        
        if query.data.startswith('buy_backtest:'):
            # Extract backtest data from callback
            _, backtest_type, days = query.data.split(':')
            
            # Ask for investment amount
            await query.message.reply_text(
                "💰 Enter the total amount to invest using the format:\n"
                f"/invest {backtest_type} {days} <amount>\n"
                "Example: /invest portfolio 5 1000"
            )

    async def invest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle investment based on backtest results"""
        try:
            if len(context.args) != 3:
                await update.message.reply_text(
                    "❌ Invalid format. Use:\n"
                    "/invest <backtest_type> <days> <amount>\n"
                    "Example: /invest portfolio 5 1000"
                )
                return
                
            backtest_type, days, amount = context.args
            days = int(days)
            amount = float(amount)
            
            # Check if we have cached results from the previous backtest
            if backtest_type.lower() == 'portfolio' and hasattr(self, '_last_portfolio_backtest'):
                cached_backtest = self._last_portfolio_backtest
                
                # Verify days parameter matches
                if cached_backtest['days'] == days:
                    # Use the allocations directly from the cached result
                    allocations = cached_backtest['allocations']
                    
                    # Get crypto symbols with their allocations
                    crypto_symbols = [s for s in allocations.keys() 
                                    if TRADING_SYMBOLS.get(s, {}).get('market') == 'CRYPTO' 
                                    and allocations.get(s, 0) > 0]
                    
                    if not crypto_symbols:
                        await update.message.reply_text("❌ No crypto assets with non-zero allocations in the backtest portfolio")
                        return
                    
                    # Show planned allocations
                    allocation_msg = "📊 Planned allocations:\n"
                    for symbol in crypto_symbols:
                        allocation_msg += f"{symbol}: ${amount * allocations[symbol]:.2f} ({allocations[symbol]*100:.1f}%)\n"
                    await update.message.reply_text(allocation_msg)
                    
                    # First close all existing crypto positions
                    for symbol in crypto_symbols:
                        # Use close_command directly
                        context.args = [symbol]  # Set the symbol as argument
                        await self.close_command(update, context)
                    
                    # Now open new positions
                    status_message = await update.message.reply_text("🔄 Opening new positions...")
                    
                    for symbol in crypto_symbols:
                        # Calculate amount for this symbol
                        symbol_amount = amount * allocations[symbol]
                        
                        # Use open_command directly
                        context.args = [symbol, str(symbol_amount)]  # Set symbol and amount as arguments
                        await self.open_command(update, context)
                    
                    await status_message.edit_text("✅ Portfolio reallocation completed!")
                    return
            
            # If no cached results or different days parameter, run a new backtest
            await update.message.reply_text("No matching backtest data found. Please run /backtest portfolio first.")
                        
        except ValueError as e:
            await update.message.reply_text(f"❌ Invalid input: {str(e)}")
        except Exception as e:
            logger.error(f"Error in invest command: {str(e)}")
            await update.message.reply_text(f"❌ An error occurred: {str(e)}")
