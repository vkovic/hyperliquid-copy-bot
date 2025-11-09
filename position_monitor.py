import time
import os
from datetime import datetime
from typing import Dict
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Rich imports for beautiful terminal UI
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from rich.layout import Layout
from rich.box import ROUNDED

# Load .env file if it exists (optional, for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed, will use system environment variables
    pass

# Initialize Hyperliquid API
info = Info(constants.MAINNET_API_URL, skip_ws=True)

# Load credentials from environment variables
TARGET_ADDRESS = os.getenv("TARGET_ADDRESS", "")

class PositionMonitor:
    def __init__(self, target_address: str):
        """
        Initialize the Position Monitor.
        
        Args:
            target_address: The address to monitor positions from
        """
        self.target_address = target_address
        self.console = Console()
        
        # Get metadata for coin info
        meta = info.meta()
        self.universe = {asset['name']: asset for asset in meta['universe']}
        
        # Position tracking
        self.target_positions: Dict[str, dict] = {}
        self.previous_target_positions: Dict[str, dict] = {}
        self.initial_target_positions: Dict[str, dict] = {}  # Snapshot of positions at start
        
        # Track entry times locally (coin -> datetime)
        self.position_entry_times: Dict[str, datetime] = {}
        
        # Track position changes for target account
        self.position_changes = []  # History of all position changes
        
        # Account balance
        self.target_account_value = 0
        
        # Stats
        self.session_start = time.time()
        self.is_initial_sync = True  # Flag to track first sync
        
    def update_account_value(self):
        """Update account value for target account."""
        try:
            target_state = info.user_state(self.target_address)
            if target_state and 'marginSummary' in target_state:
                self.target_account_value = float(target_state['marginSummary'].get('accountValue', 0))
        except Exception as e:
            self.console.print(f"[red]Error updating account value: {e}[/red]")
    
    def get_positions(self, address: str) -> Dict[str, dict]:
        """Get current positions for an address."""
        try:
            user_state = info.user_state(address)
            positions = {}
            
            if user_state and 'assetPositions' in user_state:
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
                    margin_used = float(position.get('marginUsed', 0))
                    unrealized_pnl = float(position.get('unrealizedPnl', 0))
                    
                    # Leverage info
                    leverage_info = position.get('leverage', {})
                    if isinstance(leverage_info, dict):
                        leverage = float(leverage_info.get('value', 0))
                    else:
                        leverage = position_value / margin_used if margin_used > 0 else 0
                    
                    # Side determination
                    side = "short" if szi < 0 else "long"
                    
                    positions[coin] = {
                        'coin': coin,
                        'side': side,
                        'size': abs(szi),
                        'entry_price': entry_px,
                        'notional': position_value,
                        'margin': margin_used,
                        'leverage': leverage,
                        'raw_szi': szi,
                        'unrealized_pnl': unrealized_pnl
                    }
            
            return positions
            
        except Exception as e:
            self.console.print(f"[red]Error getting positions for {address}: {e}[/red]")
            return {}
    
    def monitor_positions(self):
        """Main monitoring logic - track position changes."""
        try:
            # Update account value
            self.update_account_value()
            
            # Get current positions
            current_positions = self.get_positions(self.target_address)
            
            # If this is the initial sync, just store the baseline
            if self.is_initial_sync:
                self.initial_target_positions = current_positions.copy()
                self.target_positions = current_positions
                self.previous_target_positions = current_positions.copy()
                self.is_initial_sync = False
                return  # Don't process anything on first sync
            
            # Track entry times for NEW positions only (not in initial snapshot)
            current_time = datetime.now()
            for coin in current_positions.keys():
                if coin not in self.position_entry_times and coin not in self.initial_target_positions:
                    # This is a NEW position opened after script started
                    self.position_entry_times[coin] = current_time
            
            # Clean up entry times for closed positions
            closed_coins = set(self.position_entry_times.keys()) - set(current_positions.keys())
            for coin in closed_coins:
                del self.position_entry_times[coin]
            
            # Detect position changes
            if self.previous_target_positions:
                self._detect_position_changes(current_positions)
            
            # Update state
            self.target_positions = current_positions
            self.previous_target_positions = current_positions.copy()
            
        except Exception as e:
            self.console.print(f"[red]Error in monitor_positions: {e}[/red]")
    
    def _detect_position_changes(self, current_positions: Dict[str, dict]):
        """Detect and log changes to target account positions."""
        current_time = datetime.now()
        
        # Check for new positions
        for coin, pos in current_positions.items():
            if coin not in self.previous_target_positions:
                self.position_changes.append({
                    'time': current_time,
                    'coin': coin,
                    'action': 'OPENED',
                    'side': pos['side'].upper(),
                    'size': pos['size'],
                    'price': pos['entry_price'],
                    'leverage': pos['leverage']
                })
        
        # Check for closed positions
        for coin, prev_pos in self.previous_target_positions.items():
            if coin not in current_positions:
                self.position_changes.append({
                    'time': current_time,
                    'coin': coin,
                    'action': 'CLOSED',
                    'side': prev_pos['side'].upper(),
                    'size': prev_pos['size'],
                    'price': 0,
                    'leverage': 0
                })
        
        # Check for position changes (size or side)
        for coin, pos in current_positions.items():
            if coin in self.previous_target_positions:
                prev_pos = self.previous_target_positions[coin]
                
                # Check if side flipped
                if pos['side'] != prev_pos['side']:
                    self.position_changes.append({
                        'time': current_time,
                        'coin': coin,
                        'action': 'FLIPPED',
                        'side': f"{prev_pos['side'].upper()}→{pos['side'].upper()}",
                        'size': pos['size'],
                        'price': pos['entry_price'],
                        'leverage': pos['leverage']
                    })
                # Check if size changed significantly (>1% difference)
                elif abs(pos['size'] - prev_pos['size']) > prev_pos['size'] * 0.01:
                    size_diff = pos['size'] - prev_pos['size']
                    if size_diff > 0:
                        action = 'INCREASED'
                    else:
                        action = 'DECREASED'
                    
                    self.position_changes.append({
                        'time': current_time,
                        'coin': coin,
                        'action': action,
                        'side': pos['side'].upper(),
                        'size': abs(size_diff),
                        'price': pos['entry_price'],
                        'leverage': pos['leverage']
                    })
    
    def create_header(self):
        """Create header panel."""
        session_time = int(time.time() - self.session_start)
        position_count = len(self.target_positions)
        
        # Calculate total notional and PNL
        total_notional = sum(pos['notional'] for pos in self.target_positions.values())
        total_pnl = sum(pos['unrealized_pnl'] for pos in self.target_positions.values())
        pnl_style = "bold green" if total_pnl >= 0 else "bold red"
        pnl_sign = "-" if total_pnl < 0 else ""
        
        header_text = (
            f"[bold cyan]👁️  HYPERLIQUID POSITION MONITOR[/bold cyan]\n"
            f"Target: [yellow]{self.target_address[:8]}...{self.target_address[-6:]}[/yellow] | "
            f"Account Value: [green]${self.target_account_value:,.2f}[/green] | "
            f"Positions: [cyan]{position_count}[/cyan] | "
            f"Total Notional: [yellow]${total_notional:,.0f}[/yellow] | "
            f"Total PNL: [{pnl_style}]${pnl_sign}{abs(total_pnl):,.2f}[/{pnl_style}]\n"
            f"Session Time: {session_time//3600:02d}:{(session_time//60)%60:02d}:{session_time%60:02d}"
        )
        
        return Panel(header_text, border_style="cyan", box=ROUNDED, title="[bold cyan]Position Monitor v1.0[/bold cyan]")
    
    def create_all_positions_table(self):
        """Create table showing ALL target positions."""
        table = Table(
            title="🎯 ALL TARGET POSITIONS",
            show_header=True,
            header_style="bold yellow",
            border_style="yellow",
            box=ROUNDED
        )
        
        table.add_column("Coin", style="bold yellow", width=8)
        table.add_column("Side", width=6)
        table.add_column("Size", justify="right", width=14)
        table.add_column("Entry", justify="right", width=12)
        table.add_column("Lev", justify="right", width=6)
        table.add_column("Notional", justify="right", width=13)
        table.add_column("Margin", justify="right", width=12)
        table.add_column("PNL", justify="right", width=13)
        table.add_column("Time", style="dim", width=16)
        
        if not self.target_positions:
            table.add_row("", "No positions", "", "", "", "", "", "", "")
        else:
            for coin in sorted(self.target_positions.keys()):
                pos = self.target_positions[coin]
                
                # Color code side
                side_style = "green" if pos['side'] == "long" else "red"
                
                # Get unrealized PNL
                unrealized_pnl = pos.get('unrealized_pnl', 0)
                pnl_style = "bold green" if unrealized_pnl >= 0 else "bold red"
                pnl_sign = "-" if unrealized_pnl < 0 else ""
                pnl_display = f"[{pnl_style}]${pnl_sign}{abs(unrealized_pnl):,.2f}[/{pnl_style}]"
                
                # Format entry time
                # Show N/A for pre-existing positions, real time for new ones
                if coin in self.initial_target_positions:
                    # Pre-existing position from when script started
                    time_display = "N/A"
                else:
                    # New position opened after script started
                    entry_time = self.position_entry_times.get(coin)
                    if entry_time:
                        time_display = entry_time.strftime("%m/%d %H:%M:%S")
                    else:
                        time_display = "N/A"
                
                table.add_row(
                    coin,
                    f"[{side_style}]{pos['side'].upper()}[/{side_style}]",
                    f"{pos['size']:.4f}",
                    f"${pos['entry_price']:,.2f}",
                    f"{pos['leverage']:.1f}x",
                    f"${pos['notional']:,.0f}",
                    f"${pos['margin']:,.2f}",
                    pnl_display,
                    time_display
                )
        
        return table
    
    def create_position_changes_table(self):
        """Create table showing target account position changes."""
        table = Table(
            title="📈 TARGET POSITION CHANGES",
            show_header=True,
            header_style="bold magenta",
            border_style="magenta",
            box=ROUNDED
        )
        
        table.add_column("Time", style="cyan", width=16)
        table.add_column("Coin", style="bold yellow", width=8)
        table.add_column("Action", width=10)
        table.add_column("Side", width=12)
        table.add_column("Size", justify="right", width=14)
        table.add_column("Price", justify="right", width=12)
        table.add_column("Lev", justify="right", width=5)
        
        recent_changes = list(self.position_changes)[-25:]  # Last 25 changes
        
        if not recent_changes:
            table.add_row("No position changes yet", "", "", "", "", "", "")
        else:
            for change in recent_changes:
                # Color code actions
                action = change['action']
                if action == 'OPENED':
                    action_styled = f"[bold green]{action}[/bold green]"
                elif action == 'CLOSED':
                    action_styled = f"[bold red]{action}[/bold red]"
                elif action == 'INCREASED':
                    action_styled = f"[green]{action}[/green]"
                elif action == 'DECREASED':
                    action_styled = f"[yellow]{action}[/yellow]"
                elif action == 'FLIPPED':
                    action_styled = f"[bold magenta]{action}[/bold magenta]"
                else:
                    action_styled = action
                
                # Color code side
                side = change['side']
                if 'LONG' in side:
                    side_styled = f"[green]{side}[/green]"
                elif 'SHORT' in side:
                    side_styled = f"[red]{side}[/red]"
                else:
                    side_styled = side
                
                table.add_row(
                    change['time'].strftime("%m/%d %H:%M:%S"),
                    change['coin'],
                    action_styled,
                    side_styled,
                    f"{change['size']:.4f}",
                    f"${change['price']:,.2f}" if change['price'] > 0 else "-",
                    f"{change['leverage']:.0f}x" if change['leverage'] > 0 else "-"
                )
        
        return table
    
    def create_layout(self):
        """Create the full dashboard layout."""
        layout = Layout()
        
        layout.split_column(
            Layout(self.create_header(), name="header", size=5),
            Layout(self.create_all_positions_table(), name="positions"),
            Layout(self.create_position_changes_table(), name="position_changes", size=16)
        )
        
        return layout
    
    def run(self, refresh_interval: int = 5):
        """Run the position monitor with live dashboard."""
        self.console.print(f"[bold cyan]Starting Position Monitor...[/bold cyan]")
        self.console.print(f"[yellow]Monitoring: {self.target_address}[/yellow]")
        self.console.print(f"[yellow]Refresh Interval: {refresh_interval} seconds[/yellow]")
        self.console.print(f"[green]Press Ctrl+C to stop[/green]\n")
        time.sleep(2)
        
        # Initial fetch
        self.monitor_positions()
        time.sleep(1)
        
        # Start live dashboard
        with Live(self.create_layout(), refresh_per_second=1, screen=True) as live:
            try:
                last_sync = time.time()
                while True:
                    current_time = time.time()
                    
                    # Monitor positions at interval
                    if current_time - last_sync >= refresh_interval:
                        self.monitor_positions()
                        last_sync = current_time
                    
                    # Update display
                    live.update(self.create_layout())
                    time.sleep(0.5)
                    
            except KeyboardInterrupt:
                self.console.print("\n[red]🛑 Stopping position monitor...[/red]")


def main():
    """Main function to run the position monitor."""
    console = Console()
    
    console.print("[bold cyan]👁️  Hyperliquid Position Monitor[/bold cyan]\n")
    
    # Validate required environment variables
    if not TARGET_ADDRESS:
        console.print("[red]❌ Error: TARGET_ADDRESS environment variable not set[/red]")
        console.print("[yellow]Please set it in your .env file or export it:[/yellow]")
        console.print("[yellow]  export TARGET_ADDRESS='0xtarget_address'[/yellow]")
        return
    
    target_address = TARGET_ADDRESS
    console.print(f"[cyan]Target Address:[/cyan] {target_address}")
    
    # Hardcoded refresh interval
    refresh_interval = 5
    console.print(f"[cyan]Refresh Interval:[/cyan] {refresh_interval} seconds")
    
    console.print("\n[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print(f"[cyan]Target:[/cyan] {target_address}")
    console.print(f"[cyan]Refresh:[/cyan] {refresh_interval}s")
    console.print("[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print("[green]Starting in 2 seconds...[/green]\n")
    time.sleep(2)
    
    # Create and run monitor
    try:
        monitor = PositionMonitor(target_address)
        monitor.run(refresh_interval)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

