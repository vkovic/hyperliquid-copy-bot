import json
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account

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

class PositionCopier:
    def __init__(self, target_address: str, private_key: str, copy_mode: str = "proportional", copy_ratio: float = 1.0):
        """
        Initialize the Position Copier.
        
        Args:
            target_address: The address to copy positions from
            private_key: Your private key for trading
            copy_mode: "exact" (copy exact sizes) or "proportional" (scale by copy_ratio)
            copy_ratio: Ratio to scale positions (e.g., 0.5 = 50% of target's size)
        """
        self.target_address = target_address
        self.console = Console()
        self.copy_mode = copy_mode
        self.copy_ratio = copy_ratio
        
        # Initialize your trading account
        self.account = Account.from_key(private_key)
        self.my_address = self.account.address
        self.exchange = Exchange(self.account, constants.MAINNET_API_URL)
        
        # Get metadata for coin info
        meta = info.meta()
        self.universe = {asset['name']: asset for asset in meta['universe']}
        
        # Position tracking
        self.target_positions: Dict[str, dict] = {}
        self.my_positions: Dict[str, dict] = {}
        self.pending_orders: List[dict] = []
        self.executed_copies: List[dict] = []
        
        # Threading
        self.lock = threading.RLock()
        self.running = False
        
        # Stats
        self.stats = {
            "positions_copied": 0,
            "positions_closed": 0,
            "total_volume": 0,
            "errors": 0,
            "session_start": time.time()
        }
        
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
                    
                    # Leverage info
                    leverage_info = position.get('leverage', {})
                    if isinstance(leverage_info, dict):
                        leverage = float(leverage_info.get('value', 0))
                        leverage_type = leverage_info.get('type', 'cross')
                    else:
                        leverage = position_value / margin_used if margin_used > 0 else 0
                        leverage_type = 'cross'
                    
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
                        'leverage_type': leverage_type,
                        'raw_szi': szi
                    }
            
            return positions
            
        except Exception as e:
            self.console.print(f"[red]Error getting positions for {address}: {e}[/red]")
            return {}
    
    def calculate_copy_size(self, target_position: dict) -> float:
        """Calculate the size to copy based on copy mode and ratio."""
        target_size = target_position['size']
        
        if self.copy_mode == "exact":
            return target_size
        elif self.copy_mode == "proportional":
            return target_size * self.copy_ratio
        else:
            return target_size
    
    def place_order(self, coin: str, is_buy: bool, size: float, leverage: int = None, reduce_only: bool = False, leverage_type: str = 'cross'):
        """Place a market order to copy a position."""
        try:
            # Get current market price
            all_mids = info.all_mids()
            current_price = float(all_mids.get(coin, 0))
            
            if current_price == 0:
                self.console.print(f"[red]Cannot get price for {coin}[/red]")
                return False
            
            # Set leverage if specified
            if leverage and not reduce_only:
                try:
                    self.exchange.update_leverage(leverage, coin, is_cross=(leverage_type == 'cross'))
                    self.console.print(f"[yellow]Set leverage to {leverage}x for {coin}[/yellow]")
                except Exception as e:
                    self.console.print(f"[yellow]Could not set leverage: {e}[/yellow]")
            
            # Prepare order
            order = {
                "coin": coin,
                "is_buy": is_buy,
                "sz": size,
                "limit_px": current_price * (1.02 if is_buy else 0.98),  # 2% slippage tolerance
                "order_type": {"limit": {"tif": "Ioc"}},  # Immediate or Cancel
                "reduce_only": reduce_only
            }
            
            # Place the order
            result = self.exchange.order(order)
            
            if result and result.get('status') == 'ok':
                self.console.print(f"[green]✓ Order executed: {coin} {'BUY' if is_buy else 'SELL'} {size}[/green]")
                
                with self.lock:
                    self.stats['total_volume'] += size * current_price
                
                return True
            else:
                self.console.print(f"[red]✗ Order failed: {result}[/red]")
                with self.lock:
                    self.stats['errors'] += 1
                return False
                
        except Exception as e:
            self.console.print(f"[red]Error placing order: {e}[/red]")
            import traceback
            traceback.print_exc()
            with self.lock:
                self.stats['errors'] += 1
            return False
    
    def copy_position(self, coin: str, target_position: dict):
        """Copy a position from the target."""
        try:
            copy_size = self.calculate_copy_size(target_position)
            is_buy = target_position['side'] == 'long'
            leverage = int(target_position['leverage'])
            leverage_type = target_position.get('leverage_type', 'cross')
            
            self.console.print(f"[cyan]Copying position: {coin} {target_position['side'].upper()} {copy_size}[/cyan]")
            
            # Place the order
            success = self.place_order(coin, is_buy, copy_size, leverage, leverage_type=leverage_type)
            
            if success:
                with self.lock:
                    self.stats['positions_copied'] += 1
                    self.executed_copies.append({
                        'time': datetime.now(),
                        'coin': coin,
                        'side': target_position['side'],
                        'size': copy_size,
                        'leverage': leverage,
                        'entry_price': target_position['entry_price']
                    })
            
            return success
            
        except Exception as e:
            self.console.print(f"[red]Error copying position: {e}[/red]")
            return False
    
    def close_position(self, coin: str, my_position: dict):
        """Close a position that the target no longer has."""
        try:
            # Close by placing opposite order
            is_buy = my_position['side'] == 'short'  # If we're short, we buy to close
            size = my_position['size']
            
            self.console.print(f"[yellow]Closing position: {coin} {my_position['side'].upper()} {size}[/yellow]")
            
            # Place reduce-only order to close
            success = self.place_order(coin, is_buy, size, reduce_only=True)
            
            if success:
                with self.lock:
                    self.stats['positions_closed'] += 1
            
            return success
            
        except Exception as e:
            self.console.print(f"[red]Error closing position: {e}[/red]")
            return False
    
    def adjust_position(self, coin: str, target_position: dict, my_position: dict):
        """Adjust an existing position to match target."""
        try:
            target_size = self.calculate_copy_size(target_position)
            my_size = my_position['size']
            
            # Check if side changed
            if target_position['side'] != my_position['side']:
                # Need to close and reverse
                self.console.print(f"[yellow]Side changed for {coin}, closing and reversing...[/yellow]")
                self.close_position(coin, my_position)
                time.sleep(1)  # Wait a moment for close to complete
                self.copy_position(coin, target_position)
                return
            
            # Check if size changed significantly (>5% difference)
            size_diff = abs(target_size - my_size)
            if size_diff > target_size * 0.05:
                # Adjust size
                if target_size > my_size:
                    # Need to add to position
                    add_size = target_size - my_size
                    is_buy = target_position['side'] == 'long'
                    leverage_type = target_position.get('leverage_type', 'cross')
                    self.console.print(f"[cyan]Increasing {coin} position by {add_size}[/cyan]")
                    self.place_order(coin, is_buy, add_size, leverage=int(target_position['leverage']), leverage_type=leverage_type)
                else:
                    # Need to reduce position
                    reduce_size = my_size - target_size
                    is_buy = target_position['side'] == 'short'  # Opposite to reduce
                    self.console.print(f"[cyan]Reducing {coin} position by {reduce_size}[/cyan]")
                    self.place_order(coin, is_buy, reduce_size, reduce_only=True)
            
        except Exception as e:
            self.console.print(f"[red]Error adjusting position: {e}[/red]")
    
    def sync_positions(self):
        """Main sync logic - compare and copy positions."""
        try:
            # Get current positions
            target_positions = self.get_positions(self.target_address)
            my_positions = self.get_positions(self.my_address)
            
            with self.lock:
                self.target_positions = target_positions
                self.my_positions = my_positions
            
            # Find new positions to copy
            for coin, target_pos in target_positions.items():
                if coin not in my_positions:
                    # New position - copy it
                    self.console.print(f"[bold green]NEW POSITION DETECTED: {coin}[/bold green]")
                    self.copy_position(coin, target_pos)
                    time.sleep(1)  # Small delay between orders
                else:
                    # Position exists - check if adjustment needed
                    self.adjust_position(coin, target_pos, my_positions[coin])
            
            # Find positions to close
            for coin, my_pos in my_positions.items():
                if coin not in target_positions:
                    # Target closed this position
                    self.console.print(f"[bold yellow]POSITION CLOSED BY TARGET: {coin}[/bold yellow]")
                    self.close_position(coin, my_pos)
                    time.sleep(1)  # Small delay between orders
            
        except Exception as e:
            self.console.print(f"[red]Error in sync_positions: {e}[/red]")
            import traceback
            traceback.print_exc()
    
    def create_header(self):
        """Create header panel."""
        with self.lock:
            session_time = int(time.time() - self.stats['session_start'])
            
            header_text = (
                f"[bold cyan]🔄 HYPERLIQUID POSITION COPIER[/bold cyan]\n"
                f"Target: [yellow]{self.target_address[:8]}...{self.target_address[-6:]}[/yellow] | "
                f"Your Address: [green]{self.my_address[:8]}...{self.my_address[-6:]}[/green]\n"
                f"Mode: [yellow]{self.copy_mode.upper()}[/yellow] | "
                f"Ratio: [yellow]{self.copy_ratio:.2%}[/yellow] | "
                f"Session: {session_time//3600:02d}:{(session_time//60)%60:02d}:{session_time%60:02d}"
            )
        
        return Panel(header_text, border_style="cyan", box=ROUNDED)
    
    def create_positions_table(self):
        """Create comparison table of positions."""
        table = Table(
            title="📊 POSITIONS COMPARISON",
            show_header=True,
            header_style="bold cyan",
            border_style="cyan",
            box=ROUNDED
        )
        
        table.add_column("Coin", style="bold yellow", width=8)
        table.add_column("Target Side", width=10)
        table.add_column("Target Size", justify="right", width=12)
        table.add_column("Target Lev", justify="right", width=8)
        table.add_column("Your Side", width=10)
        table.add_column("Your Size", justify="right", width=12)
        table.add_column("Your Lev", justify="right", width=8)
        table.add_column("Status", width=12)
        
        with self.lock:
            all_coins = set(list(self.target_positions.keys()) + list(self.my_positions.keys()))
            
            if not all_coins:
                table.add_row("No positions", "", "", "", "", "", "", "")
            else:
                for coin in sorted(all_coins):
                    target_pos = self.target_positions.get(coin)
                    my_pos = self.my_positions.get(coin)
                    
                    target_side = target_pos['side'].upper() if target_pos else "-"
                    target_size = f"{target_pos['size']:.4f}" if target_pos else "-"
                    target_lev = f"{target_pos['leverage']:.1f}x" if target_pos else "-"
                    
                    my_side = my_pos['side'].upper() if my_pos else "-"
                    my_size = f"{my_pos['size']:.4f}" if my_pos else "-"
                    my_lev = f"{my_pos['leverage']:.1f}x" if my_pos else "-"
                    
                    # Determine status
                    if target_pos and my_pos:
                        if target_pos['side'] == my_pos['side']:
                            status = "[green]✓ SYNCED[/green]"
                        else:
                            status = "[red]⚠ MISMATCH[/red]"
                    elif target_pos and not my_pos:
                        status = "[yellow]⏳ COPYING[/yellow]"
                    elif not target_pos and my_pos:
                        status = "[yellow]⏳ CLOSING[/yellow]"
                    else:
                        status = "-"
                    
                    # Color code sides
                    target_side_colored = f"[green]{target_side}[/green]" if target_side == "LONG" else f"[red]{target_side}[/red]" if target_side == "SHORT" else target_side
                    my_side_colored = f"[green]{my_side}[/green]" if my_side == "LONG" else f"[red]{my_side}[/red]" if my_side == "SHORT" else my_side
                    
                    table.add_row(
                        coin,
                        target_side_colored,
                        target_size,
                        target_lev,
                        my_side_colored,
                        my_size,
                        my_lev,
                        status
                    )
        
        return table
    
    def create_history_table(self):
        """Create table of recently executed copies."""
        table = Table(
            title="📝 RECENT COPIES",
            show_header=True,
            header_style="bold green",
            border_style="green",
            box=ROUNDED
        )
        
        table.add_column("Time", style="cyan", width=19)
        table.add_column("Coin", style="bold yellow", width=8)
        table.add_column("Side", width=8)
        table.add_column("Size", justify="right", width=12)
        table.add_column("Leverage", justify="right", width=8)
        table.add_column("Entry", justify="right", width=12)
        
        with self.lock:
            recent_copies = list(self.executed_copies)[-15:]  # Last 15
            
            if not recent_copies:
                table.add_row("No copies yet", "", "", "", "", "")
            else:
                for copy in recent_copies:
                    side_style = "green" if copy['side'] == "long" else "red"
                    
                    table.add_row(
                        copy['time'].strftime("%Y-%m-%d %H:%M:%S"),
                        copy['coin'],
                        f"[{side_style}]{copy['side'].upper()}[/{side_style}]",
                        f"{copy['size']:.4f}",
                        f"{copy['leverage']:.1f}x",
                        f"${copy['entry_price']:,.4f}"
                    )
        
        return table
    
    def create_stats_panel(self):
        """Create statistics panel."""
        with self.lock:
            stats_text = (
                f"📊 Session Stats:\n"
                f"Positions Copied: [green]{self.stats['positions_copied']}[/green] | "
                f"Positions Closed: [yellow]{self.stats['positions_closed']}[/yellow] | "
                f"Total Volume: [green]${self.stats['total_volume']:,.2f}[/green] | "
                f"Errors: [red]{self.stats['errors']}[/red]"
            )
        
        return Panel(stats_text, border_style="blue", box=ROUNDED)
    
    def create_layout(self):
        """Create the full dashboard layout."""
        layout = Layout()
        
        layout.split_column(
            Layout(self.create_header(), name="header", size=5),
            Layout(self.create_positions_table(), name="positions"),
            Layout(self.create_history_table(), name="history", size=20),
            Layout(self.create_stats_panel(), name="stats", size=4)
        )
        
        return layout
    
    def run(self, refresh_interval: int = 10):
        """Run the position copier with live dashboard."""
        self.console.print(f"[cyan]Starting Position Copier...[/cyan]")
        self.console.print(f"[yellow]Monitoring: {self.target_address}[/yellow]")
        self.console.print(f"[yellow]Your Address: {self.my_address}[/yellow]")
        self.console.print(f"[yellow]Copy Mode: {self.copy_mode} ({self.copy_ratio:.1%})[/yellow]")
        self.console.print(f"[yellow]Refresh Interval: {refresh_interval} seconds[/yellow]")
        self.console.print(f"[green]Press Ctrl+C to stop[/green]\n")
        
        self.running = True
        
        # Initial sync
        self.sync_positions()
        
        # Start live dashboard
        with Live(self.create_layout(), refresh_per_second=1, screen=True) as live:
            try:
                last_sync = time.time()
                while self.running:
                    current_time = time.time()
                    
                    # Sync positions at interval
                    if current_time - last_sync >= refresh_interval:
                        self.sync_positions()
                        last_sync = current_time
                    
                    # Update display
                    live.update(self.create_layout())
                    time.sleep(0.5)
                    
            except KeyboardInterrupt:
                self.console.print("\n[red]🛑 Stopping position copier...[/red]")
                self.running = False


def main():
    """Main function to run the position copier."""
    console = Console()
    
    console.print("[bold cyan]🔄 Hyperliquid Position Copier[/bold cyan]\n")
    
    # Get target address
    console.print("[yellow]Enter the address to copy positions from:[/yellow]")
    target_address = input("Target Address: ").strip()
    
    if not target_address:
        console.print("[red]Error: No address provided[/red]")
        return
    
    # Validate address format
    if not target_address.startswith("0x") or len(target_address) != 42:
        console.print("[yellow]Warning: Address format looks unusual[/yellow]")
    
    # Get your private key
    console.print("\n[yellow]Enter your private key (for placing orders):[/yellow]")
    console.print("[red]⚠️  WARNING: Keep your private key secure! This script will use it to trade.[/red]")
    private_key = input("Private Key: ").strip()
    
    if not private_key:
        console.print("[red]Error: No private key provided[/red]")
        return
    
    # Get copy mode
    console.print("\n[yellow]Choose copy mode:[/yellow]")
    console.print("  1. Exact - Copy exact position sizes")
    console.print("  2. Proportional - Scale positions by a ratio")
    mode_choice = input("Mode (1/2): ").strip()
    
    if mode_choice == "1":
        copy_mode = "exact"
        copy_ratio = 1.0
    elif mode_choice == "2":
        copy_mode = "proportional"
        console.print("[yellow]Enter copy ratio (e.g., 0.5 for 50%, 2.0 for 200%):[/yellow]")
        try:
            copy_ratio = float(input("Ratio: ").strip())
        except ValueError:
            console.print("[red]Invalid ratio, using 1.0[/red]")
            copy_ratio = 1.0
    else:
        console.print("[yellow]Invalid choice, using exact mode[/yellow]")
        copy_mode = "exact"
        copy_ratio = 1.0
    
    # Get refresh interval
    console.print("\n[yellow]Enter refresh interval in seconds (default: 10):[/yellow]")
    try:
        refresh_input = input("Interval: ").strip()
        refresh_interval = int(refresh_input) if refresh_input else 10
    except ValueError:
        refresh_interval = 10
    
    # Confirm before starting
    console.print("\n[bold yellow]⚠️  CONFIRMATION[/bold yellow]")
    console.print(f"Target: {target_address}")
    console.print(f"Mode: {copy_mode}")
    console.print(f"Ratio: {copy_ratio:.1%}")
    console.print(f"Refresh: {refresh_interval}s")
    console.print("\n[red]This script will automatically place trades on your account![/red]")
    confirm = input("Type 'YES' to continue: ").strip()
    
    if confirm != "YES":
        console.print("[yellow]Cancelled[/yellow]")
        return
    
    # Create and run copier
    try:
        copier = PositionCopier(target_address, private_key, copy_mode, copy_ratio)
        copier.run(refresh_interval)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

