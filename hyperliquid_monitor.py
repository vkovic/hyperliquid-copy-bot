import json
import time
import threading
import hashlib
from collections import deque
from datetime import datetime
from hyperliquid.info import Info
from hyperliquid.utils import constants
from hyperliquid.websocket_manager import WebsocketManager

# Rich imports for dashboard UI
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.text import Text
from rich.box import ROUNDED

# Threshold for "big move" (USD notional value)
BIG_MOVE_THRESHOLD = 50000  # $50K

# Get metadata once for coin prices (in USD)
info = Info(constants.MAINNET_API_URL, skip_ws=True)
meta = info.meta()
universe = {asset['name']: asset for asset in meta['universe']}

# Dashboard class for Rich UI
class TradingDashboard:
    def __init__(self):
        self.console = Console()
        self.big_trades = deque(maxlen=20)  # Keep last 20 big trades (chronological)
        self.all_big_trades = []  # Keep ALL big trades for "biggest trades" ranking
        self.stats = {
            "total_trades": 0,
            "total_big_trades": 0,
            "total_volume": 0,
            "session_start": time.time()
        }
        self.lock = threading.RLock()  # Use RLock for reentrant locking
        self.monitored_coins = []

    def add_big_trade(self, trade_data):
        """Add a big trade to the dashboard."""
        with self.lock:
            trade_entry = {
                'time': datetime.now().strftime("%H:%M:%S"),
                'coin': trade_data['coin'],
                'side': trade_data['side'],
                'size': trade_data['size'],
                'price': trade_data['px'],
                'notional': trade_data['notional'],
                'trader': trade_data['trader']
            }
            self.big_trades.append(trade_entry)
            self.all_big_trades.append(trade_entry)  # Also add to permanent list
            self.stats['total_big_trades'] += 1
            self.stats['total_volume'] += trade_data['notional']
    
    def get_biggest_trades(self, limit=15):
        """Get the biggest trades sorted by notional value."""
        with self.lock:
            # Sort all big trades by notional value (descending)
            sorted_trades = sorted(self.all_big_trades, key=lambda x: x['notional'], reverse=True)
            return sorted_trades[:limit]

    def increment_trade_count(self):
        """Increment total trade counter."""
        with self.lock:
            self.stats['total_trades'] += 1

    def set_monitored_coins(self, coins):
        """Set the list of monitored coins."""
        with self.lock:
            self.monitored_coins = coins
    
    def reset_state(self):
        """Reset all dashboard state for a fresh start."""
        with self.lock:
            self.big_trades.clear()
            self.all_big_trades.clear()
            self.stats = {
                "total_trades": 0,
                "total_big_trades": 0,
                "total_volume": 0,
                "session_start": time.time()
            }
            self.monitored_coins = []

    def create_layout(self):
        """Create the dashboard layout."""
        with self.lock:
            # Header with title and session info
            session_time = int(time.time() - self.stats['session_start'])
            header_text = "🎯 HYPERLIQUID MONITOR"
            session_info = f"Session: {session_time//3600:02d}:{(session_time//60)%60:02d}:{session_time%60:02d} | Monitoring: {len(self.monitored_coins)} coins"
            
            header_panel = Panel(
                f"[bold cyan]{header_text}[/bold cyan]\n{session_info}",
                border_style="cyan",
                box=ROUNDED
            )

            # Big Trades Table (Left side)
            big_trades_table = Table(
                title="🚨 Recent Big Trades",
                show_header=True,
                header_style="bold red",
                border_style="red",
                box=ROUNDED
            )
            big_trades_table.add_column("Time", style="cyan", width=8)
            big_trades_table.add_column("Coin", style="bold yellow", width=6)
            big_trades_table.add_column("Side", width=4)
            big_trades_table.add_column("Size", style="white", width=10)
            big_trades_table.add_column("Price", style="white", width=10)
            big_trades_table.add_column("Notional", style="bold green", width=12, justify="right")
            big_trades_table.add_column("Trader", style="magenta", width=12)

            for trade in list(self.big_trades)[-15:]:  # Show last 15
                side_style = "bold green" if trade['side'] == "BUY" else "bold red"
                big_trades_table.add_row(
                    trade['time'],
                    trade['coin'],
                    f"[{side_style}]{trade['side']}[/{side_style}]",
                    str(trade['size']),
                    str(trade['price']),
                    f"${trade['notional']:,.0f}",
                    trade['trader'][:12] + "..." if len(trade['trader']) > 12 else trade['trader']
                )

            # Biggest Trades Table (Right side) - sorted by notional value
            biggest_trades_table = Table(
                title="📊 Biggest Trades (Top 15)",
                show_header=True,
                header_style="bold yellow",
                border_style="yellow",
                box=ROUNDED
            )
            biggest_trades_table.add_column("Time", style="cyan", width=8)
            biggest_trades_table.add_column("Coin", style="bold yellow", width=6)
            biggest_trades_table.add_column("Side", width=4)
            biggest_trades_table.add_column("Size", style="white", width=10)
            biggest_trades_table.add_column("Price", style="white", width=10)
            biggest_trades_table.add_column("Notional", style="bold green", width=12, justify="right")
            biggest_trades_table.add_column("Trader", style="magenta", width=12)

            for trade in self.get_biggest_trades(15):
                side_style = "bold green" if trade['side'] == "BUY" else "bold red"
                biggest_trades_table.add_row(
                    trade['time'],
                    trade['coin'],
                    f"[{side_style}]{trade['side']}[/{side_style}]",
                    str(trade['size']),
                    str(trade['price']),
                    f"${trade['notional']:,.0f}",
                    trade['trader'][:12] + "..." if len(trade['trader']) > 12 else trade['trader']
                )

            # Main content with two tables side by side
            main_content = Columns([big_trades_table, biggest_trades_table])

            # Footer with statistics
            stats_text = (
                f"📊 Session Stats: "
                f"Total Trades: {self.stats['total_trades']:,} | "
                f"Big Trades: {self.stats['total_big_trades']:,} | "
                f"Total Volume: ${self.stats['total_volume']:,.0f}"
            )
            
            footer_panel = Panel(
                stats_text,
                border_style="blue",
                box=ROUNDED
            )

            # Create final layout using Layout properly
            layout = Layout()
            layout.split_column(
                Layout(header_panel, name="header", size=3),
                Layout(main_content, name="main"),
                Layout(footer_panel, name="footer", size=3)
            )

            return layout

# Create global dashboard instance
dashboard = TradingDashboard()

def reset_all_state():
    """Reset all global state for a completely fresh start."""
    # Reset dashboard state
    dashboard.reset_state()
    
    print("🔄 State reset - starting fresh session")

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

def on_trade(trade):
    """Callback for new trades. Alert on big moves."""
    current_time = time.time()
    notional = calculate_notional(trade)
    
    # Increment total trade count
    dashboard.increment_trade_count()
    
    # Extract trade information
    side = "BUY" if trade['side'] == "B" else "SELL"  # B = Buy, A = Sell in Hyperliquid
    timestamp = trade['time']
    coin = trade['coin']
    size = trade['sz']
    px = trade['px']
    
    # Try different possible field names for user address
    user_address = 'unknown'
    possible_user_fields = ['user', 'trader', 'address', 'account', 'wallet', 'usr', 'uid', 'hash', 'tid']
    for field in possible_user_fields:
        if field in trade and trade[field]:
            user_address = str(trade[field])
            break
    
    # If still unknown, check if there are any hex-like fields that could be addresses
    if user_address == 'unknown':
        for key, value in trade.items():
            if isinstance(value, str) and len(value) >= 20:
                # Check if it's hex-like (could be an address or hash)
                if all(c in '0123456789abcdefABCDEFx' for c in value.replace('0x', '')):
                    user_address = value
                    break
    
    # If we still don't have user info, create a pseudo-identifier from trade details
    if user_address == 'unknown':
        # Create a short hash from trade details for tracking
        trade_signature = f"{timestamp}{coin}{size}{px}"
        pseudo_id = hashlib.md5(trade_signature.encode()).hexdigest()[:8]
        user_address = f"T-{pseudo_id}"  # T for "Trade"
    
    # Check for individual big move alert
    if notional > BIG_MOVE_THRESHOLD:
        # Format trader display name
        if len(user_address) > 15:
            trader_display = user_address[:12] + "..."
        else:
            trader_display = user_address
            
        trade_data = {
            'coin': coin,
            'side': side,
            'size': size,
            'px': px,
            'notional': notional,
            'trader': trader_display
        }
        dashboard.add_big_trade(trade_data)

def handle_trades_message(ws_msg):
    """Handle incoming trades WebSocket messages."""
    if ws_msg["channel"] == "trades" and "data" in ws_msg:
        trades = ws_msg["data"]
        for trade in trades:
            on_trade(trade)

def run_monitor():
    # Reset all state for a fresh start
    reset_all_state()
    
    # Get all available coins from Hyperliquid API (from the universe metadata)
    available_coins = sorted(list(universe.keys()))
    
    # Set monitored coins in dashboard
    dashboard.set_monitored_coins(available_coins)
    
    # Create WebSocket manager
    ws_manager = WebsocketManager(constants.MAINNET_API_URL)
    ws_manager.start()
    
    # Show initial setup info
    console = Console()
    console.print(f"[cyan]Setting up Hyperliquid Monitor...[/cyan]")
    console.print(f"[green]Monitoring {len(available_coins)} coins: {', '.join(available_coins)}[/green]")
    
    try:
        # Subscribe to trades for each popular coin
        for coin in available_coins:
            subscription = {"type": "trades", "coin": coin}
            ws_manager.subscribe(subscription, handle_trades_message)
            console.print(f"[yellow]Subscribed to {coin} trades[/yellow]")
        
        console.print(f"[cyan]🎯 Monitoring Configuration:[/cyan]")
        console.print(f"[cyan]   • Tracking trades > ${BIG_MOVE_THRESHOLD:,} USD[/cyan]")
        console.print(f"[cyan]   • Recent trades: Last 15 chronological | Biggest trades: Top 15 by size[/cyan]")
        console.print(f"[green]Starting live dashboard... Press Ctrl+C to stop[/green]")
        
        # Give a moment for subscriptions to establish
        time.sleep(2)
        
        # Start the live dashboard
        with Live(dashboard.create_layout(), refresh_per_second=2, screen=True) as live:
            try:
                while True:
                    live.update(dashboard.create_layout())
                    time.sleep(0.5)  # Update every 500ms
            except KeyboardInterrupt:
                console.print("\n[red]🛑 Stopping monitor...[/red]")
            
    except KeyboardInterrupt:
        console.print("\n[red]🛑 Stopping monitor...[/red]")
    finally:
        ws_manager.stop()

if __name__ == "__main__":
    run_monitor()
