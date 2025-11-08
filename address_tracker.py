import json
import time
import threading
from datetime import datetime
from hyperliquid.info import Info
from hyperliquid.utils import constants
from hyperliquid.websocket_manager import WebsocketManager

# Rich imports for beautiful terminal UI
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from rich.layout import Layout
from rich.box import ROUNDED
from rich.text import Text

# Initialize Hyperliquid API
info = Info(constants.MAINNET_API_URL, skip_ws=True)

class AddressTracker:
    def __init__(self, address):
        self.address = address
        self.console = Console()
        self.futures_positions = []
        self.spot_holdings = []
        self.recent_trades = []
        self.account_value = 0
        self.total_pnl = 0
        self.lock = threading.RLock()
        self.last_update = None
        
        # Get metadata for coin info
        meta = info.meta()
        self.universe = {asset['name']: asset for asset in meta['universe']}
        self.spot_meta = info.spot_meta()
        self.spot_universe = {token['name']: token for token in self.spot_meta.get('tokens', [])}
        
    def update_data(self):
        """Fetch latest data from Hyperliquid API."""
        try:
            # Get user state (futures positions)
            user_state = info.user_state(self.address)
            
            with self.lock:
                self.futures_positions = []
                self.spot_holdings = []
                self.total_pnl = 0
                
                if user_state:
                    # Account value
                    self.account_value = float(user_state.get('marginSummary', {}).get('accountValue', 0))
                    
                    # Process futures positions
                    if 'assetPositions' in user_state:
                        for asset_pos in user_state['assetPositions']:
                            position = asset_pos.get('position', {})
                            coin = position.get('coin')
                            
                            if not coin:
                                continue
                            
                            # Get position size (can be negative for shorts)
                            szi = float(position.get('szi', 0))
                            
                            if szi == 0:
                                continue  # No active position
                            
                            # Position details
                            entry_px = float(position.get('entryPx', 0))
                            position_value = float(position.get('positionValue', 0))
                            unrealized_pnl = float(position.get('unrealizedPnl', 0))
                            margin_used = float(position.get('marginUsed', 0))
                            liquidation_px = position.get('liquidationPx')
                            
                            # Get current mark price
                            mark_px = 0
                            if coin in self.universe:
                                try:
                                    all_mids = info.all_mids()
                                    mark_px = float(all_mids.get(coin, 0))
                                except:
                                    mark_px = 0
                            
                            # Leverage info
                            leverage_info = position.get('leverage', {})
                            if isinstance(leverage_info, dict):
                                leverage = float(leverage_info.get('value', 0))
                                leverage_type = leverage_info.get('type', 'cross')
                            else:
                                leverage = position_value / margin_used if margin_used > 0 else 0
                                leverage_type = 'cross'
                            
                            # ROI calculation
                            roi = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0
                            
                            # Side determination
                            side = "SHORT" if szi < 0 else "LONG"
                            
                            self.futures_positions.append({
                                'coin': coin,
                                'side': side,
                                'size': abs(szi),
                                'entry_price': entry_px,
                                'mark_price': mark_px,
                                'notional': position_value,
                                'unrealized_pnl': unrealized_pnl,
                                'margin': margin_used,
                                'leverage': leverage,
                                'leverage_type': leverage_type,
                                'liquidation_px': liquidation_px,
                                'roi': roi
                            })
                            
                            self.total_pnl += unrealized_pnl
                
                # Get spot balances
                try:
                    spot_state = info.spot_user_state(self.address)
                    if spot_state and 'balances' in spot_state:
                        for balance in spot_state['balances']:
                            coin = balance.get('coin', '')
                            hold = float(balance.get('hold', 0))
                            total = float(balance.get('total', 0))
                            
                            if total > 0:
                                # Try to get USD value
                                usd_value = 0
                                try:
                                    # Get token info for USD value calculation
                                    token_info = self.spot_universe.get(coin, {})
                                    # You might need to fetch current price here
                                    # For now, we'll just show the amount
                                except:
                                    pass
                                
                                self.spot_holdings.append({
                                    'coin': coin,
                                    'total': total,
                                    'available': total - hold,
                                    'hold': hold,
                                    'usd_value': usd_value
                                })
                except Exception as e:
                    self.console.print(f"[yellow]Could not fetch spot holdings: {e}[/yellow]")
                
                self.last_update = datetime.now()
                
        except Exception as e:
            self.console.print(f"[red]Error updating data: {e}[/red]")
            import traceback
            traceback.print_exc()
    
    def get_recent_fills(self, limit=20):
        """Get recent trade fills for this address."""
        try:
            # Get user fills (trades history)
            fills = info.user_fills(self.address)
            
            with self.lock:
                self.recent_trades = []
                
                if fills and len(fills) > 0:
                    # Sort by time (most recent first)
                    sorted_fills = sorted(fills, key=lambda x: x.get('time', 0), reverse=True)
                    
                    for fill in sorted_fills[:limit]:
                        coin = fill.get('coin', '')
                        side = "BUY" if fill.get('side') == "B" else "SELL"
                        size = abs(float(fill.get('sz', 0)))
                        price = float(fill.get('px', 0))
                        timestamp = fill.get('time', 0)
                        
                        # Calculate notional
                        sz_decimals = self.universe.get(coin, {}).get('szDecimals', 0)
                        adjusted_size = size / (10 ** sz_decimals) if sz_decimals else size
                        notional = adjusted_size * price
                        
                        # Convert timestamp to readable format
                        dt = datetime.fromtimestamp(timestamp / 1000.0)
                        
                        self.recent_trades.append({
                            'time': dt,
                            'coin': coin,
                            'side': side,
                            'size': adjusted_size,
                            'price': price,
                            'notional': notional,
                            'fee': float(fill.get('fee', 0)),
                            'closed_pnl': float(fill.get('closedPnl', 0))
                        })
        
        except Exception as e:
            self.console.print(f"[red]Error fetching fills: {e}[/red]")
    
    def create_futures_table(self):
        """Create a table for futures positions."""
        table = Table(
            title="🔮 FUTURES POSITIONS",
            show_header=True,
            header_style="bold cyan",
            border_style="cyan",
            box=ROUNDED,
            title_style="bold cyan"
        )
        
        table.add_column("Coin", style="bold yellow", width=8)
        table.add_column("Side", width=6)
        table.add_column("Size", justify="right", width=12)
        table.add_column("Entry", justify="right", width=10)
        table.add_column("Mark", justify="right", width=10)
        table.add_column("Notional", style="bold", justify="right", width=12)
        table.add_column("PNL", justify="right", width=12)
        table.add_column("ROI%", justify="right", width=10)
        table.add_column("Margin", justify="right", width=11)
        table.add_column("Lev", justify="right", width=7)
        table.add_column("Liq Price", justify="right", width=10)
        
        with self.lock:
            if not self.futures_positions:
                table.add_row("No active positions", "", "", "", "", "", "", "", "", "", "")
            else:
                for pos in self.futures_positions:
                    # Color coding
                    side_style = "bold green" if pos['side'] == "LONG" else "bold red"
                    pnl_style = "bold green" if pos['unrealized_pnl'] >= 0 else "bold red"
                    roi_style = "bold green" if pos['roi'] >= 0 else "bold red"
                    lev_style = "bold red" if pos['leverage'] >= 20 else "bold yellow" if pos['leverage'] >= 10 else "white"
                    
                    # Format liquidation price
                    liq_px_str = f"${float(pos['liquidation_px']):,.2f}" if pos['liquidation_px'] else "N/A"
                    
                    table.add_row(
                        pos['coin'],
                        f"[{side_style}]{pos['side']}[/{side_style}]",
                        f"{pos['size']:.4f}",
                        f"${pos['entry_price']:,.4f}",
                        f"${pos['mark_price']:,.4f}",
                        f"${pos['notional']:,.2f}",
                        f"[{pnl_style}]${pos['unrealized_pnl']:+,.2f}[/{pnl_style}]",
                        f"[{roi_style}]{pos['roi']:+.2f}%[/{roi_style}]",
                        f"${pos['margin']:,.2f}",
                        f"[{lev_style}]{pos['leverage']:.1f}x[/{lev_style}]",
                        liq_px_str
                    )
        
        return table
    
    def create_spot_table(self):
        """Create a table for spot holdings."""
        table = Table(
            title="💰 SPOT HOLDINGS",
            show_header=True,
            header_style="bold green",
            border_style="green",
            box=ROUNDED,
            title_style="bold green"
        )
        
        table.add_column("Coin", style="bold yellow", width=12)
        table.add_column("Total", justify="right", width=18)
        table.add_column("Available", justify="right", width=18)
        table.add_column("On Hold", justify="right", width=18)
        table.add_column("USD Value", justify="right", width=15)
        
        with self.lock:
            if not self.spot_holdings:
                table.add_row("No spot holdings", "", "", "", "")
            else:
                for holding in self.spot_holdings:
                    usd_str = f"${holding['usd_value']:,.2f}" if holding['usd_value'] > 0 else "N/A"
                    table.add_row(
                        holding['coin'],
                        f"{holding['total']:.8f}",
                        f"{holding['available']:.8f}",
                        f"{holding['hold']:.8f}",
                        usd_str
                    )
        
        return table
    
    def create_trades_table(self):
        """Create a table for recent trades."""
        table = Table(
            title="📊 RECENT TRADES (Last 20)",
            show_header=True,
            header_style="bold magenta",
            border_style="magenta",
            box=ROUNDED,
            title_style="bold magenta"
        )
        
        table.add_column("Time", style="cyan", width=19)
        table.add_column("Coin", style="bold yellow", width=8)
        table.add_column("Side", width=5)
        table.add_column("Size", justify="right", width=14)
        table.add_column("Price", justify="right", width=12)
        table.add_column("Notional", justify="right", width=12)
        table.add_column("Fee", justify="right", width=10)
        table.add_column("Closed PNL", justify="right", width=12)
        
        with self.lock:
            if not self.recent_trades:
                table.add_row("No recent trades", "", "", "", "", "", "", "")
            else:
                for trade in self.recent_trades:
                    side_style = "bold green" if trade['side'] == "BUY" else "bold red"
                    pnl_style = "bold green" if trade['closed_pnl'] >= 0 else "bold red"
                    
                    table.add_row(
                        trade['time'].strftime("%Y-%m-%d %H:%M:%S"),
                        trade['coin'],
                        f"[{side_style}]{trade['side']}[/{side_style}]",
                        f"{trade['size']:.6f}",
                        f"${trade['price']:,.4f}",
                        f"${trade['notional']:,.2f}",
                        f"${trade['fee']:,.4f}",
                        f"[{pnl_style}]${trade['closed_pnl']:+,.2f}[/{pnl_style}]"
                    )
        
        return table
    
    def create_header(self):
        """Create header panel with account summary."""
        with self.lock:
            update_time = self.last_update.strftime("%Y-%m-%d %H:%M:%S") if self.last_update else "N/A"
            pnl_style = "bold green" if self.total_pnl >= 0 else "bold red"
            
            header_text = (
                f"[bold cyan]📈 HYPERLIQUID ADDRESS TRACKER[/bold cyan]\n"
                f"Address: [yellow]{self.address[:8]}...{self.address[-6:]}[/yellow] | "
                f"Account Value: [bold green]${self.account_value:,.2f}[/bold green] | "
                f"Total Unrealized PNL: [{pnl_style}]${self.total_pnl:+,.2f}[/{pnl_style}]\n"
                f"Last Update: {update_time}"
            )
        
        return Panel(header_text, border_style="cyan", box=ROUNDED)
    
    def create_layout(self):
        """Create the full dashboard layout."""
        layout = Layout()
        
        layout.split_column(
            Layout(self.create_header(), name="header", size=5),
            Layout(self.create_futures_table(), name="futures"),
            Layout(self.create_spot_table(), name="spot", size=10),
            Layout(self.create_trades_table(), name="trades")
        )
        
        return layout
    
    def run_live_monitor(self, refresh_interval=5):
        """Run live monitoring with auto-refresh."""
        self.console.print(f"[cyan]Starting live monitor for address: {self.address}[/cyan]")
        self.console.print(f"[yellow]Refresh interval: {refresh_interval} seconds[/yellow]")
        self.console.print(f"[green]Press Ctrl+C to stop[/green]\n")
        
        # Initial data fetch
        self.update_data()
        self.get_recent_fills(20)
        
        # Start live dashboard
        with Live(self.create_layout(), refresh_per_second=1, screen=True) as live:
            try:
                last_refresh = time.time()
                while True:
                    current_time = time.time()
                    
                    # Refresh data every interval
                    if current_time - last_refresh >= refresh_interval:
                        self.update_data()
                        self.get_recent_fills(20)
                        last_refresh = current_time
                    
                    # Update display
                    live.update(self.create_layout())
                    time.sleep(0.5)
                    
            except KeyboardInterrupt:
                self.console.print("\n[red]🛑 Stopping monitor...[/red]")


def main():
    """Main function to run the tracker."""
    console = Console()
    
    # Prompt for address
    console.print("[bold cyan]🔍 Hyperliquid Address Tracker[/bold cyan]")
    console.print("[yellow]Enter the address you want to track:[/yellow]")
    
    address = input("Address: ").strip()
    
    if not address:
        console.print("[red]Error: No address provided[/red]")
        return
    
    # Validate address format (basic check)
    if not address.startswith("0x") or len(address) != 42:
        console.print("[yellow]Warning: Address format looks unusual. Expected format: 0x...[/yellow]")
        response = input("Continue anyway? (y/n): ").strip().lower()
        if response != 'y':
            return
    
    # Create tracker and run
    tracker = AddressTracker(address)
    tracker.run_live_monitor(refresh_interval=5)


if __name__ == "__main__":
    main()

