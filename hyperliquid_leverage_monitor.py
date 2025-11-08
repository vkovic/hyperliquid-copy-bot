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

# Threshold for "big move" (USD margin/capital at risk)
BIG_MOVE_THRESHOLD = 10000  # $10K margin minimum

# Get metadata once for coin prices (in USD)
info = Info(constants.MAINNET_API_URL, skip_ws=True)
meta = info.meta()
universe = {asset['name']: asset for asset in meta['universe']}

# Cache for user leverage data to avoid excessive API calls
leverage_cache = {}
leverage_cache_lock = threading.Lock()
CACHE_EXPIRY = 60  # Cache leverage data for 60 seconds

# Dashboard class for Rich UI
class LeverageDashboard:
    def __init__(self):
        self.console = Console()
        self.leveraged_trades = deque(maxlen=20)  # Keep last 20 leveraged trades
        self.highest_leverage_trades = []  # Keep ALL trades for ranking by leverage
        self.stats = {
            "total_trades": 0,
            "total_leveraged_trades": 0,
            "total_volume": 0,
            "avg_leverage": 0,
            "max_leverage": 0,
            "session_start": time.time()
        }
        self.lock = threading.RLock()
        self.monitored_coins = []

    def add_leveraged_trade(self, trade_data):
        """Add a leveraged trade to the dashboard."""
        with self.lock:
            trade_entry = {
                'time': datetime.now().strftime("%H:%M:%S"),
                'coin': trade_data['coin'],
                'side': trade_data['side'],
                'size': trade_data['size'],
                'price': trade_data['px'],
                'notional': trade_data['notional'],
                'trader': trade_data['trader'],
                'leverage': trade_data.get('leverage', 0),
                'position_value': trade_data.get('position_value', 0),
                'margin': trade_data.get('margin', 0)
            }
            self.leveraged_trades.append(trade_entry)
            self.highest_leverage_trades.append(trade_entry)
            self.stats['total_leveraged_trades'] += 1
            self.stats['total_volume'] += trade_data['notional']
            
            # Update max leverage
            if trade_entry['leverage'] > self.stats['max_leverage']:
                self.stats['max_leverage'] = trade_entry['leverage']
            
            # Update average leverage
            if self.stats['total_leveraged_trades'] > 0:
                total_lev = sum(t['leverage'] for t in self.highest_leverage_trades if t['leverage'] > 0)
                count = len([t for t in self.highest_leverage_trades if t['leverage'] > 0])
                self.stats['avg_leverage'] = total_lev / count if count > 0 else 0
    
    def get_highest_leverage_trades(self, limit=15):
        """Get trades with leverage info, sorted by notional value."""
        with self.lock:
            # Filter trades with leverage > 0 and sort by notional (descending)
            leveraged = [t for t in self.highest_leverage_trades if t['leverage'] > 0]
            sorted_trades = sorted(leveraged, key=lambda x: x['notional'], reverse=True)
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
            self.leveraged_trades.clear()
            self.highest_leverage_trades.clear()
            self.stats = {
                "total_trades": 0,
                "total_leveraged_trades": 0,
                "total_volume": 0,
                "avg_leverage": 0,
                "max_leverage": 0,
                "session_start": time.time()
            }
            self.monitored_coins = []

    def create_layout(self):
        """Create the dashboard layout."""
        with self.lock:
            # Header with title and session info
            session_time = int(time.time() - self.stats['session_start'])
            header_text = "📊 HYPERLIQUID LEVERAGE MONITOR"
            session_info = f"Session: {session_time//3600:02d}:{(session_time//60)%60:02d}:{session_time%60:02d} | Monitoring: {len(self.monitored_coins)} coins"
            
            header_panel = Panel(
                f"[bold cyan]{header_text}[/bold cyan]\n{session_info}",
                border_style="cyan",
                box=ROUNDED
            )

            # Recent Leveraged Trades Table (Left side)
            recent_trades_table = Table(
                title="🔥 Recent Leveraged Trades",
                show_header=True,
                header_style="bold red",
                border_style="red",
                box=ROUNDED
            )
            recent_trades_table.add_column("Time", style="cyan", width=8)
            recent_trades_table.add_column("Coin", style="bold yellow", width=6)
            recent_trades_table.add_column("Side", width=4)
            recent_trades_table.add_column("Notional", style="bold green", width=11, justify="right")
            recent_trades_table.add_column("Lev", style="bold magenta", width=6, justify="right")
            recent_trades_table.add_column("Margin", style="white", width=10, justify="right")
            recent_trades_table.add_column("Trader", style="magenta", width=44)

            for trade in list(self.leveraged_trades)[-15:]:  # Show last 15
                side_style = "bold green" if trade['side'] == "BUY" else "bold red"
                lev_style = "bold red" if trade['leverage'] >= 20 else "bold yellow" if trade['leverage'] >= 10 else "white"
                recent_trades_table.add_row(
                    trade['time'],
                    trade['coin'],
                    f"[{side_style}]{trade['side']}[/{side_style}]",
                    f"${trade['notional']:,.0f}",
                    f"[{lev_style}]{trade['leverage']:.1f}x[/{lev_style}]" if trade['leverage'] > 0 else "N/A",
                    f"${trade['margin']:,.0f}" if trade['margin'] > 0 else "N/A",
                    trade['trader']
                )

            # Biggest Leveraged Trades Table (Right side)
            highest_lev_table = Table(
                title="⚡ Biggest Leveraged Trades (Top 15)",
                show_header=True,
                header_style="bold yellow",
                border_style="yellow",
                box=ROUNDED
            )
            highest_lev_table.add_column("Time", style="cyan", width=8)
            highest_lev_table.add_column("Coin", style="bold yellow", width=6)
            highest_lev_table.add_column("Side", width=4)
            highest_lev_table.add_column("Notional", style="bold green", width=11, justify="right")
            highest_lev_table.add_column("Lev", style="bold magenta", width=6, justify="right")
            highest_lev_table.add_column("Margin", style="white", width=10, justify="right")
            highest_lev_table.add_column("Trader", style="magenta", width=44)

            for trade in self.get_highest_leverage_trades(15):
                side_style = "bold green" if trade['side'] == "BUY" else "bold red"
                lev_style = "bold red" if trade['leverage'] >= 20 else "bold yellow" if trade['leverage'] >= 10 else "white"
                highest_lev_table.add_row(
                    trade['time'],
                    trade['coin'],
                    f"[{side_style}]{trade['side']}[/{side_style}]",
                    f"${trade['notional']:,.0f}",
                    f"[{lev_style}]{trade['leverage']:.1f}x[/{lev_style}]",
                    f"${trade['margin']:,.0f}" if trade['margin'] > 0 else "N/A",
                    trade['trader']
                )

            # Main content with two tables side by side
            main_content = Columns([recent_trades_table, highest_lev_table])

            # Footer with statistics
            stats_text = (
                f"📊 Session Stats: "
                f"Total Trades: {self.stats['total_trades']:,} | "
                f"Leveraged Trades: {self.stats['total_leveraged_trades']:,} | "
                f"Total Volume: ${self.stats['total_volume']:,.0f} | "
                f"Avg Leverage: {self.stats['avg_leverage']:.2f}x | "
                f"Max Leverage: {self.stats['max_leverage']:.1f}x"
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
dashboard = LeverageDashboard()

def reset_all_state():
    """Reset all global state for a completely fresh start."""
    # Reset dashboard state
    dashboard.reset_state()
    
    # Clear leverage cache
    with leverage_cache_lock:
        leverage_cache.clear()
    
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

def get_user_leverage(user_address, coin):
    """Fetch user's leverage for a specific coin position."""
    cache_key = f"{user_address}_{coin}"
    current_time = time.time()
    
    # Check cache first
    with leverage_cache_lock:
        if cache_key in leverage_cache:
            cached_data, cached_time = leverage_cache[cache_key]
            if current_time - cached_time < CACHE_EXPIRY:
                return cached_data
    
    try:
        # Query user state from API
        user_state = info.user_state(user_address)
        
        if not user_state or 'assetPositions' not in user_state:
            return {'leverage': 0, 'position_value': 0, 'margin': 0}
        
        # Find the position for this coin
        for asset_pos in user_state['assetPositions']:
            position_data = asset_pos.get('position', {})
            
            if position_data.get('coin') == coin:
                # Get position size (can be negative for shorts)
                szi = float(position_data.get('szi', 0))
                
                if szi == 0:
                    # No position in this coin
                    continue
                
                # Get leverage directly from position data
                leverage_info = position_data.get('leverage', {})
                if isinstance(leverage_info, dict):
                    leverage = float(leverage_info.get('value', 0))
                    leverage_type = leverage_info.get('type', 'unknown')
                else:
                    leverage = 0
                    leverage_type = 'unknown'
                
                # Get position value and margin used
                position_value = float(position_data.get('positionValue', 0))
                margin_used = float(position_data.get('marginUsed', 0))
                
                # If leverage is 0 but we have position value and margin, calculate it
                if leverage == 0 and position_value > 0 and margin_used > 0:
                    leverage = position_value / margin_used
                
                result = {
                    'leverage': leverage,
                    'leverage_type': leverage_type,
                    'position_value': position_value,
                    'margin': margin_used,
                    'position_size': abs(szi),
                    'is_short': szi < 0
                }
                
                # Cache the result
                with leverage_cache_lock:
                    leverage_cache[cache_key] = (result, current_time)
                
                return result
        
        # No position found for this coin
        return {'leverage': 0, 'position_value': 0, 'margin': 0}
    
    except Exception as e:
        # If API call fails, return zeros
        print(f"[ERROR] Failed to get leverage for {user_address}: {e}")
        import traceback
        traceback.print_exc()
        return {'leverage': 0, 'position_value': 0, 'margin': 0}

def on_trade(trade):
    """Callback for new trades. Alert on big moves with leverage."""
    current_time = time.time()
    notional = calculate_notional(trade)
    
    # Increment total trade count
    dashboard.increment_trade_count()
    
    # Extract trade information
    side = "BUY" if trade['side'] == "B" else "SELL"
    timestamp = trade['time']
    coin = trade['coin']
    size = trade['sz']
    px = trade['px']
    
    # Extract user address from 'users' array
    user_address = 'unknown'
    if 'users' in trade and isinstance(trade['users'], list) and len(trade['users']) > 0:
        # Take first user (typically the taker/aggressor)
        user_address = trade['users'][0]
    
    # If we still don't have user info, create a pseudo-identifier from trade details
    if user_address == 'unknown':
        # Create a short hash from trade details for tracking
        trade_signature = f"{timestamp}{coin}{size}{px}"
        pseudo_id = hashlib.md5(trade_signature.encode()).hexdigest()[:8]
        user_address = f"T-{pseudo_id}"
    
    # Check if this could be a big margin trade
    # Since margin = notional / leverage, and max leverage is typically 50x,
    # we need notional >= BIG_MOVE_THRESHOLD to possibly have margin >= BIG_MOVE_THRESHOLD
    # (at 1x leverage, margin = notional)
    if notional >= BIG_MOVE_THRESHOLD and not user_address.startswith('T-'):
        # Use full trader address
        trader_display = user_address
        
        # Get leverage info in a separate thread to avoid blocking
        def fetch_and_add_trade():
            leverage_info = get_user_leverage(user_address, coin)
            
            # Calculate trade margin based on trade notional and position leverage
            trade_margin = 0
            if leverage_info['leverage'] > 0:
                trade_margin = notional / leverage_info['leverage']
            else:
                # If no leverage info, assume 1x (margin = notional)
                trade_margin = notional
            
            # Only add if margin meets threshold
            if trade_margin >= BIG_MOVE_THRESHOLD:
                trade_data = {
                    'coin': coin,
                    'side': side,
                    'size': size,
                    'px': px,
                    'notional': notional,
                    'trader': trader_display,
                    'leverage': leverage_info['leverage'] if leverage_info['leverage'] > 0 else 1.0,
                    'position_value': leverage_info['position_value'],
                    'margin': trade_margin
                }
                dashboard.add_leveraged_trade(trade_data)
        
        # Run in thread to avoid blocking the WebSocket handler
        threading.Thread(target=fetch_and_add_trade, daemon=True).start()

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
    console.print(f"[cyan]Setting up Hyperliquid Leverage Monitor...[/cyan]")
    console.print(f"[green]Monitoring {len(available_coins)} coins: {', '.join(available_coins[:10])}...[/green]")
    
    try:
        # Subscribe to trades for each coin
        for coin in available_coins:
            subscription = {"type": "trades", "coin": coin}
            ws_manager.subscribe(subscription, handle_trades_message)
        
        console.print(f"[cyan]🎯 Monitoring Configuration:[/cyan]")
        console.print(f"[cyan]   • Tracking trades with MARGIN ≥ ${BIG_MOVE_THRESHOLD:,} USD (actual capital at risk)[/cyan]")
        console.print(f"[cyan]   • Fetching leverage data for each trader[/cyan]")
        console.print(f"[cyan]   • Recent trades: Last 15 chronological | Biggest: Top 15 by notional[/cyan]")
        console.print(f"[yellow]   • Note: Leverage queries may be slower due to API calls[/yellow]")
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

