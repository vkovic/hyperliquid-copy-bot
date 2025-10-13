import json
import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from hyperliquid.info import Info
from hyperliquid.utils import constants
from hyperliquid.websocket_manager import WebsocketManager

# Threshold for "big move" (USD notional value) - REDUCED FOR TESTING
BIG_MOVE_THRESHOLD = 1000  # $1K (was $100K)

# Cumulative tracking settings - REDUCED FOR TESTING
CUMULATIVE_THRESHOLD = 2500  # $2.5K cumulative in time window (was $250K)
TIME_WINDOW_MINUTES = 5  # Track trades over 5 minutes (reduced for testing)
CLEANUP_INTERVAL = 60  # Clean up old data every 60 seconds

# Data structures for tracking cumulative trades
user_trades_lock = threading.Lock()
user_trades = defaultdict(deque)  # user_address -> deque of (timestamp, notional, trade_info)
last_cleanup = time.time()

# Get metadata once for coin prices (in USD)
info = Info(constants.MAINNET_API_URL, skip_ws=True)
meta = info.meta()
universe = {asset['name']: asset for asset in meta['universe']}

def calculate_notional(fill):
    """Calculate USD notional: size * px * szDecimals adjustment * USD value per coin."""
    coin = fill['coin']
    size = abs(float(fill['sz']))  # Size is always positive
    px = float(fill['px'])  # Price in coin terms (e.g., USD for most perps)
    
    # Adjustment for decimals (e.g., BTC sz=0.01 means 0.01 BTC)
    sz_decimals = universe[coin]['szDecimals']
    adjusted_size = size / (10 ** sz_decimals)
    
    # Most Hyperliquid perps are USD-denominated, so notional ≈ adjusted_size * px
    # For precision, multiply by coin's USD value if needed (e.g., for non-USD bases)
    usd_per_coin = universe[coin].get('usdValue', 1)  # Fallback to 1 for USD pairs
    notional = adjusted_size * px * usd_per_coin
    return notional

def cleanup_old_trades():
    """Remove trades older than TIME_WINDOW_MINUTES to prevent memory leaks."""
    global last_cleanup
    current_time = time.time()
    
    # Only cleanup every CLEANUP_INTERVAL seconds to avoid overhead
    if current_time - last_cleanup < CLEANUP_INTERVAL:
        return
    
    cutoff_time = current_time - (TIME_WINDOW_MINUTES * 60)
    
    with user_trades_lock:
        users_to_remove = []
        for user, trades in user_trades.items():
            # Remove old trades from the left of the deque
            while trades and trades[0][0] < cutoff_time:
                trades.popleft()
            
            # If no trades left for this user, mark for removal
            if not trades:
                users_to_remove.append(user)
        
        # Remove users with no recent trades
        for user in users_to_remove:
            del user_trades[user]
    
    last_cleanup = current_time

def calculate_cumulative_notional(user_address, current_time):
    """Calculate cumulative notional for a user within the time window."""
    cutoff_time = current_time - (TIME_WINDOW_MINUTES * 60)
    
    with user_trades_lock:
        trades = user_trades.get(user_address, deque())
        total_notional = sum(
            trade_data[1] for trade_data in trades 
            if trade_data[0] >= cutoff_time
        )
        return total_notional, len([t for t in trades if t[0] >= cutoff_time])

def on_trade(trade):
    """Callback for new trades. Alert on big moves and track cumulative activity."""
    current_time = time.time()
    notional = calculate_notional(trade)
    
    # Clean up old trades periodically
    cleanup_old_trades()
    
    # Extract trade information
    side = "BUY" if trade['side'] == "B" else "SELL"  # B = Buy, A = Sell in Hyperliquid
    timestamp = trade['time']
    coin = trade['coin']
    size = trade['sz']
    px = trade['px']
    user_address = trade.get('user', 'unknown')  # Some trades might not have user info
    
    # Check for individual big move alert
    individual_alert = notional > BIG_MOVE_THRESHOLD
    
    # Track cumulative trades by user (only if we have user info)
    cumulative_alert = False
    cumulative_notional = 0
    trade_count = 0
    
    if user_address != 'unknown':
        # Add this trade to user's history
        trade_info = {
            'coin': coin,
            'side': side,
            'size': size,
            'px': px,
            'notional': notional,
            'timestamp': timestamp
        }
        
        with user_trades_lock:
            user_trades[user_address].append((current_time, notional, trade_info))
        
        # Calculate cumulative position for this user
        cumulative_notional, trade_count = calculate_cumulative_notional(user_address, current_time)
        cumulative_alert = cumulative_notional > CUMULATIVE_THRESHOLD and trade_count > 1
    
    # Alert logic
    if individual_alert:
        print(f"🚨 BIG TRADE ALERT 🚨")
        print(f"Time: {timestamp}")
        print(f"Coin: {coin} | Side: {side} | Size: {size} | Price: {px}")
        print(f"Trader: {user_address[:10]}..." if user_address != 'unknown' else "Trader: Unknown")
        print(f"Notional: ${notional:,.2f} USD")
        print("---")
    
    if cumulative_alert:
        print(f"🤖 CUMULATIVE BOT ALERT 🤖")
        print(f"Trader: {user_address[:10]}...")
        print(f"Total Volume: ${cumulative_notional:,.2f} USD over {TIME_WINDOW_MINUTES} minutes")
        print(f"Trade Count: {trade_count} trades")
        print(f"Latest: {coin} {side} ${notional:,.2f}")
        print("---")

def handle_trades_message(ws_msg):
    """Handle incoming trades WebSocket messages."""
    if ws_msg["channel"] == "trades" and "data" in ws_msg:
        trades = ws_msg["data"]
        for trade in trades:
            on_trade(trade)

def run_monitor():
    # Get top coins by volume to monitor
    popular_coins = ["BTC", "ETH", "SOL", "ARB", "OP", "AVAX", "DOGE", "XRP", "ADA", "MATIC", 
                    "LINK", "UNI", "ATOM", "DOT", "FTM", "NEAR", "ICP", "APT", "SUI", "SEI"]
    
    # Filter to only coins that exist in the universe
    available_coins = [coin for coin in popular_coins if coin in universe]
    print(f"Monitoring {len(available_coins)} coins: {', '.join(available_coins)}")
    
    # Create WebSocket manager
    ws_manager = WebsocketManager(constants.MAINNET_API_URL)
    ws_manager.start()
    
    try:
        # Subscribe to trades for each popular coin
        for coin in available_coins:
            subscription = {"type": "trades", "coin": coin}
            ws_manager.subscribe(subscription, handle_trades_message)
            print(f"Subscribed to {coin} trades")
        
        print(f"🎯 Monitoring Configuration:")
        print(f"   • Individual trades > ${BIG_MOVE_THRESHOLD:,} USD")
        print(f"   • Cumulative trades > ${CUMULATIVE_THRESHOLD:,} USD (over {TIME_WINDOW_MINUTES} minutes)")
        print("Press Ctrl+C to stop\n")
        
        # Keep the main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Stopping monitor...")
    finally:
        ws_manager.stop()

if __name__ == "__main__":
    run_monitor()
