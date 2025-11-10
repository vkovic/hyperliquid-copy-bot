import json
import time
import threading
import os
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
AGENT_PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY", "")
MAIN_ACCOUNT_ADDRESS = os.getenv("MAIN_ACCOUNT_ADDRESS", "")
TARGET_ADDRESS = os.getenv("TARGET_ADDRESS", "")

# Load configuration from environment variables
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "10"))  # Default 10 seconds

class PositionCopier:
    def __init__(self, target_address: str, private_key: str, main_account_address: str = None, copy_mode: str = "proportional", copy_ratio: float = 1.0, max_position_pct: float = 30.0, auto_calculate_ratio: bool = False):
        """
        Initialize the Position Copier.
        
        Args:
            target_address: The address to copy positions from
            private_key: Your agent wallet private key for trading
            main_account_address: Your main trading account address (where funds are, optional if not using agent)
            copy_mode: "exact" (copy exact sizes) or "proportional" (scale by copy_ratio)
            copy_ratio: Ratio to scale positions (e.g., 0.5 = 50% of target's size)
            max_position_pct: Maximum % of your balance to risk on a single position (default: 30%)
            auto_calculate_ratio: Automatically calculate ratio based on account sizes
        """
        self.target_address = target_address
        self.console = Console()
        self.copy_mode = copy_mode
        self.copy_ratio = copy_ratio
        self.max_position_pct = max_position_pct
        self.auto_calculate_ratio = auto_calculate_ratio
        
        # Initialize your trading account
        self.account = Account.from_key(private_key)
        self.agent_address = self.account.address
        
        # Use main account address for info queries, or agent address if not specified
        self.my_address = main_account_address if main_account_address else self.agent_address
        
        # Exchange uses agent wallet but trades on behalf of main account
        if main_account_address:
            self.exchange = Exchange(self.account, constants.MAINNET_API_URL, account_address=main_account_address)
        else:
            self.exchange = Exchange(self.account, constants.MAINNET_API_URL)
        
        # Get metadata for coin info
        meta = info.meta()
        self.universe = {asset['name']: asset for asset in meta['universe']}
        
        # Position tracking
        self.target_positions: Dict[str, dict] = {}
        self.my_positions: Dict[str, dict] = {}
        self.initial_target_positions: Dict[str, dict] = {}  # Snapshot of positions at start
        self.previous_target_positions: Dict[str, dict] = {}  # Track previous state for changes
        self.pending_orders: List[dict] = []
        self.executed_copies: List[dict] = []
        self.is_initial_sync = True  # Flag to track first sync
        
        # Track entry times locally (coin -> datetime)
        self.position_entry_times: Dict[str, datetime] = {}
        
        # Track position changes for target account
        self.position_changes: List[dict] = []  # History of all position changes
        
        # Account balances
        self.target_account_value = 0
        self.my_account_value = 0
        self.my_available_margin = 0
        
        # Threading
        self.lock = threading.RLock()
        self.running = False
        
        # Stats
        self.stats = {
            "positions_copied": 0,
            "positions_closed": 0,
            "total_volume": 0,
            "errors": 0,
            "skipped_insufficient_funds": 0,
            "session_start": time.time()
        }
        
    def update_account_values(self):
        """Update account values for both target and your account."""
        try:
            # Get target account value
            target_state = info.user_state(self.target_address)
            if target_state and 'marginSummary' in target_state:
                self.target_account_value = float(target_state['marginSummary'].get('accountValue', 0))
            
            # Get your account value
            my_state = info.user_state(self.my_address)
            if my_state and 'marginSummary' in my_state:
                margin_summary = my_state['marginSummary']
                self.my_account_value = float(margin_summary.get('accountValue', 0))
                total_margin_used = float(margin_summary.get('totalMarginUsed', 0))
                self.my_available_margin = self.my_account_value - total_margin_used
            
            # Auto-calculate ratio if enabled (silently, display in header instead)
            if self.auto_calculate_ratio and self.target_account_value > 0:
                calculated_ratio = self.my_account_value / self.target_account_value
                # Add a safety factor (use 80% of calculated to leave buffer)
                self.copy_ratio = calculated_ratio * 0.8
                
        except Exception as e:
            self.console.print(f"[red]Error updating account values: {e}[/red]")
    
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
                        'raw_szi': szi,
                        'unrealized_pnl': unrealized_pnl
                    }
            
            return positions
            
        except Exception as e:
            self.console.print(f"[red]Error getting positions for {address}: {e}[/red]")
            return {}
    
    def calculate_copy_size(self, target_position: dict) -> tuple:
        """
        Calculate the size to copy based on copy mode and ratio.
        Returns (size, margin_required, is_safe)
        """
        target_size = target_position['size']
        
        if self.copy_mode == "exact":
            copy_size = target_size
        elif self.copy_mode == "proportional":
            copy_size = target_size * self.copy_ratio
        else:
            copy_size = target_size
        
        # Ensure copy_size is positive
        if copy_size <= 0 or target_size <= 0:
            return 0, 0, False
        
        # Calculate margin required for this position
        notional = target_position['notional'] * (copy_size / target_position['size']) if target_position['size'] > 0 else 0
        leverage = target_position['leverage'] if target_position['leverage'] > 0 else 1
        margin_required = notional / leverage if leverage > 0 else notional
        
        # Check if this position is safe (doesn't exceed max_position_pct)
        max_margin_allowed = self.my_account_value * (self.max_position_pct / 100)
        is_safe = margin_required <= max_margin_allowed and margin_required <= self.my_available_margin
        
        # If not safe, scale down the size
        if not is_safe and margin_required > 0:
            # Calculate safe size
            safe_margin = min(max_margin_allowed, self.my_available_margin * 0.9)  # Use 90% of available
            scale_factor = safe_margin / margin_required
            copy_size = copy_size * scale_factor
            margin_required = safe_margin
            
            self.console.print(f"[yellow]⚠️  Position scaled down to fit margin limits: {scale_factor*100:.1f}% of intended size[/yellow]")
        
        return copy_size, margin_required, is_safe
    
    def place_order(self, coin: str, is_buy: bool, size: float, leverage: int = None, reduce_only: bool = False, leverage_type: str = 'isolated'):
        """Place a market order to copy a position."""
        try:
            # Validate size
            if size <= 0:
                self.console.print(f"[red]Invalid order size: {size} for {coin}[/red]")
                return False
            
            # Get current market price
            all_mids = info.all_mids()
            current_price = float(all_mids.get(coin, 0))
            
            if current_price == 0:
                self.console.print(f"[red]Cannot get price for {coin}[/red]")
                return False
            
            # Check minimum notional value (~$10 minimum on most exchanges)
            notional = size * current_price
            if notional < 10:
                self.console.print(f"[yellow]⚠️  Order too small for {coin}: ${notional:.2f} (min $10). Skipping.[/yellow]")
                return False
            
            # Set leverage if specified (always isolated)
            if leverage and not reduce_only:
                try:
                    # is_cross=False means isolated margin
                    self.exchange.update_leverage(leverage, coin, is_cross=False)
                    self.console.print(f"[yellow]Set leverage to {leverage}x ISOLATED for {coin}[/yellow]")
                except Exception as e:
                    self.console.print(f"[yellow]Could not set leverage: {e}[/yellow]")
            
            # Round size according to coin's szDecimals
            sz_decimals = self.universe.get(coin, {}).get('szDecimals', 0)
            if sz_decimals:
                # Size needs to be in raw units (multiply by 10^szDecimals)
                # But check if the API expects it already adjusted
                pass  # Keep size as is for now
            
            # Calculate limit price with slippage tolerance
            limit_px = current_price * (1.02 if is_buy else 0.98)  # 2% slippage tolerance
            
            # Prepare order type (Immediate or Cancel)
            order_type = {"limit": {"tif": "Ioc"}}
            
            self.console.print(f"[dim]Placing order: {coin} {'BUY' if is_buy else 'SELL'} size={size:.8f} @ ${limit_px:.4f}[/dim]")
            
            # Place the order with correct argument structure
            result = self.exchange.order(coin, is_buy, size, limit_px, order_type, reduce_only=reduce_only)
            
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
            copy_size, margin_required, is_safe = self.calculate_copy_size(target_position)
            
            # Debug info
            self.console.print(f"[dim]Target size: {target_position['size']:.6f}, Copy ratio: {self.copy_ratio:.6f}, Copy size: {copy_size:.6f}[/dim]")
            
            # Check if copy size is too small
            if copy_size <= 0:
                self.console.print(f"[yellow]⚠️  Calculated copy size is 0 or negative for {coin}. Skipping.[/yellow]")
                return False
            
            # Check if we have enough margin
            if margin_required > self.my_available_margin:
                self.console.print(f"[red]⚠️  Insufficient margin for {coin}: Need ${margin_required:.2f}, Available ${self.my_available_margin:.2f}[/red]")
                with self.lock:
                    self.stats['skipped_insufficient_funds'] += 1
                return False
            
            is_buy = target_position['side'] == 'long'
            leverage = int(target_position['leverage']) if target_position['leverage'] > 0 else 1
            # Always use isolated margin for your positions
            leverage_type = 'isolated'
            
            pct_of_balance = (margin_required / self.my_account_value * 100) if self.my_account_value > 0 else 0
            self.console.print(f"[cyan]Copying position: {coin} {target_position['side'].upper()} {copy_size:.6f} (Margin: ${margin_required:.2f}, {pct_of_balance:.1f}% of balance)[/cyan]")
            
            # Place the order
            success = self.place_order(coin, is_buy, copy_size, leverage, leverage_type=leverage_type)
            
            if success:
                # Update available margin
                self.my_available_margin -= margin_required
                
                with self.lock:
                    self.stats['positions_copied'] += 1
                    self.executed_copies.append({
                        'time': datetime.now(),
                        'coin': coin,
                        'side': target_position['side'],
                        'size': copy_size,
                        'leverage': leverage,
                        'entry_price': target_position['entry_price'],
                        'margin_used': margin_required
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
                    # Always use isolated margin
                    leverage_type = 'isolated'
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
            # Update account values first
            self.update_account_values()
            
            # Get current positions
            target_positions = self.get_positions(self.target_address)
            my_positions = self.get_positions(self.my_address)
            
            # Track entry times for new positions
            current_time = datetime.now()
            for coin in target_positions.keys():
                if coin not in self.position_entry_times:
                    self.position_entry_times[coin] = current_time
            
            for coin in my_positions.keys():
                if coin not in self.position_entry_times:
                    self.position_entry_times[coin] = current_time
            
            # Clean up entry times for closed positions
            all_current_coins = set(list(target_positions.keys()) + list(my_positions.keys()))
            closed_coins = set(self.position_entry_times.keys()) - all_current_coins
            for coin in closed_coins:
                del self.position_entry_times[coin]
            
            # If this is the initial sync, just store the baseline and don't copy anything
            if self.is_initial_sync:
                with self.lock:
                    self.initial_target_positions = target_positions.copy()
                    self.target_positions = target_positions
                    self.my_positions = my_positions
                    self.is_initial_sync = False
                
                if target_positions:
                    coins_list = ', '.join(target_positions.keys())
                    self.console.print(f"[cyan]📸 Initial snapshot taken. Target has {len(target_positions)} existing position(s): {coins_list}[/cyan]")
                    self.console.print(f"[cyan]✓ Will only copy NEW positions opened after this point[/cyan]")
                else:
                    self.console.print(f"[cyan]📸 Initial snapshot taken. Target has no open positions.[/cyan]")
                    self.console.print(f"[cyan]✓ Will copy any positions target opens[/cyan]")
                
                return  # Don't process anything on first sync
            
            # Detect and log changes to target positions
            self._detect_target_position_changes(target_positions)
            
            with self.lock:
                self.target_positions = target_positions
                self.my_positions = my_positions
                # Update previous state for next comparison
                self.previous_target_positions = target_positions.copy()
            
            # Find new positions to copy (not in initial snapshot)
            for coin, target_pos in target_positions.items():
                # Check if this is a NEW position (not in initial snapshot)
                if coin not in self.initial_target_positions:
                    if coin not in my_positions:
                        # New position opened after script started - copy it!
                        self.console.print(f"[bold green]🆕 NEW POSITION DETECTED: {coin} (opened after script start)[/bold green]")
                        self.copy_position(coin, target_pos)
                        time.sleep(1)  # Small delay between orders
                    else:
                        # We already copied this position - check if adjustment needed
                        self.adjust_position(coin, target_pos, my_positions[coin])
                else:
                    # This was an existing position from the start - ignore it
                    pass
            
            # Find positions to close (only close positions WE copied, not pre-existing ones)
            for coin, my_pos in my_positions.items():
                # Only manage positions that we copied (not in initial snapshot)
                if coin not in self.initial_target_positions:
                    if coin not in target_positions:
                        # Target closed this position that we copied
                        self.console.print(f"[bold yellow]📉 POSITION CLOSED BY TARGET: {coin}[/bold yellow]")
                        self.close_position(coin, my_pos)
                        time.sleep(1)  # Small delay between orders
            
        except Exception as e:
            self.console.print(f"[red]Error in sync_positions: {e}[/red]")
            import traceback
            traceback.print_exc()
    
    def _detect_target_position_changes(self, current_positions: Dict[str, dict]):
        """Detect and log changes to target account positions."""
        if not self.previous_target_positions:
            # First run, no previous state to compare
            return
        
        current_time = datetime.now()
        
        with self.lock:
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
        with self.lock:
            session_time = int(time.time() - self.stats['session_start'])
            
            # Color code available margin
            margin_pct = (self.my_available_margin / self.my_account_value * 100) if self.my_account_value > 0 else 0
            margin_color = "green" if margin_pct > 50 else "yellow" if margin_pct > 20 else "red"
            
            # Count pre-existing vs new positions
            initial_count = len(self.initial_target_positions)
            current_count = len(self.target_positions)
            new_positions_count = len([k for k in self.target_positions.keys() if k not in self.initial_target_positions])
            
            # Show auto-calc status
            ratio_display = f"[yellow]{self.copy_ratio:.4f} ({self.copy_ratio*100:.2f}%)[/yellow]"
            if self.auto_calculate_ratio:
                ratio_display += " [dim cyan](AUTO)[/dim cyan]"
            
            header_text = (
                f"[bold cyan]🔄 HYPERLIQUID POSITION COPIER[/bold cyan] [yellow](NEW POSITIONS ONLY)[/yellow]\n"
                f"Target: [yellow]{self.target_address[:8]}...{self.target_address[-6:]}[/yellow] "
                f"(${self.target_account_value:,.2f}) | "
                f"Your Address: [green]{self.my_address[:8]}...{self.my_address[-6:]}[/green] "
                f"(${self.my_account_value:,.2f})\n"
                f"Available Margin: [{margin_color}]${self.my_available_margin:,.2f}[/{margin_color}] "
                f"({margin_pct:.1f}%) | "
                f"Mode: [yellow]{self.copy_mode.upper()}[/yellow] | "
                f"Ratio: {ratio_display} | "
                f"Max Per Position: [yellow]{self.max_position_pct:.0f}%[/yellow]\n"
                f"Target Positions: [cyan]Pre-existing: {initial_count}[/cyan] | "
                f"[green]New (copying): {new_positions_count}[/green] | "
                f"Session: {session_time//3600:02d}:{(session_time//60)%60:02d}:{session_time%60:02d}"
            )
        
        return Panel(header_text, border_style="cyan", box=ROUNDED, title="[bold cyan]Position Copier v2.0[/bold cyan]")
    
    def create_all_target_positions_table(self):
        """Create table showing ALL target positions (including pre-existing)."""
        table = Table(
            title="🎯 ALL TARGET POSITIONS",
            show_header=True,
            header_style="bold yellow",
            border_style="yellow",
            box=ROUNDED
        )
        
        table.add_column("Type", width=4)
        table.add_column("Coin", style="bold yellow", width=7)
        table.add_column("Side", width=6)
        table.add_column("Size", justify="right", width=14)
        table.add_column("Entry", justify="right", width=12)
        table.add_column("Lev", justify="right", width=6)
        table.add_column("Notional", justify="right", width=13)
        table.add_column("PNL", justify="right", width=13)
        table.add_column("Time", style="dim", width=16)
        
        with self.lock:
            if not self.target_positions:
                table.add_row("", "No positions", "", "", "", "", "", "", "")
            else:
                for coin in sorted(self.target_positions.keys()):
                    pos = self.target_positions[coin]
                    
                    # Determine if pre-existing or new - just use icon
                    is_new = coin not in self.initial_target_positions
                    pos_icon = "[green]+[/green]" if is_new else "[dim]📌[/dim]"
                    
                    # Color code side
                    side_style = "green" if pos['side'] == "long" else "red"
                    
                    # Get unrealized PNL
                    unrealized_pnl = pos.get('unrealized_pnl', 0)
                    pnl_style = "bold green" if unrealized_pnl >= 0 else "bold red"
                    pnl_sign = "-" if unrealized_pnl < 0 else ""
                    pnl_display = f"[{pnl_style}]${pnl_sign}{abs(unrealized_pnl):,.2f}[/{pnl_style}]"
                    
                    # Format entry time from our tracking
                    # Show N/A for pre-existing positions, real time for new ones
                    is_preexisting = coin in self.initial_target_positions
                    if is_preexisting:
                        time_display = "N/A"
                    else:
                        entry_time = self.position_entry_times.get(coin)
                        if entry_time:
                            time_display = entry_time.strftime("%m/%d %H:%M:%S")
                        else:
                            time_display = "N/A"
                    
                    table.add_row(
                        pos_icon,
                        coin,
                        f"[{side_style}]{pos['side'].upper()}[/{side_style}]",
                        f"{pos['size']:.4f}",
                        f"${pos['entry_price']:,.2f}",
                        f"{pos['leverage']:.1f}x",
                        f"${pos['notional']:,.0f}",
                        pnl_display,
                        time_display
                    )
        
        return table
    
    def create_copied_positions_table(self):
        """Create comparison table of copied positions only."""
        table = Table(
            title="📊 COPIED POSITIONS (Your Positions)",
            show_header=True,
            header_style="bold cyan",
            border_style="cyan",
            box=ROUNDED
        )
        
        table.add_column("Coin", style="bold yellow", width=7)
        table.add_column("Target", width=6)
        table.add_column("T.Size", justify="right", width=12)
        table.add_column("Your", width=6)
        table.add_column("Y.Size", justify="right", width=12)
        table.add_column("Lev", justify="right", width=5)
        table.add_column("Your PNL", justify="right", width=11)
        table.add_column("Entry Time", style="dim", width=16)
        table.add_column("Status", width=9)
        
        with self.lock:
            # Only show positions that are NEW (not in initial snapshot)
            new_target_positions = {k: v for k, v in self.target_positions.items() 
                                   if k not in self.initial_target_positions}
            relevant_my_positions = {k: v for k, v in self.my_positions.items() 
                                    if k not in self.initial_target_positions}
            
            all_coins = set(list(new_target_positions.keys()) + list(relevant_my_positions.keys()))
            
            if not all_coins:
                table.add_row("No copied positions yet", "", "", "", "", "", "", "", "")
            else:
                for coin in sorted(all_coins):
                    target_pos = new_target_positions.get(coin)
                    my_pos = relevant_my_positions.get(coin)
                    
                    target_side = target_pos['side'].upper() if target_pos else "-"
                    target_size = f"{target_pos['size']:.4f}" if target_pos else "-"
                    
                    my_side = my_pos['side'].upper() if my_pos else "-"
                    my_size = f"{my_pos['size']:.4f}" if my_pos else "-"
                    my_lev = f"{my_pos['leverage']:.1f}x" if my_pos else "-"
                    
                    # Get your PNL
                    if my_pos:
                        my_pnl = my_pos.get('unrealized_pnl', 0)
                        pnl_style = "bold green" if my_pnl >= 0 else "bold red"
                        pnl_sign = "-" if my_pnl < 0 else ""
                        pnl_display = f"[{pnl_style}]${pnl_sign}{abs(my_pnl):,.2f}[/{pnl_style}]"
                    else:
                        pnl_display = "-"
                    
                    # Get entry time from our tracking
                    # Show N/A for pre-existing positions (not being copied), real time for copied ones
                    if my_pos:
                        # Check if this was copied (not pre-existing on target)
                        is_preexisting_on_target = coin in self.initial_target_positions
                        if is_preexisting_on_target:
                            # We didn't copy this, so N/A
                            time_display = "N/A"
                        else:
                            # We copied this, show when we copied it
                            entry_time = self.position_entry_times.get(coin)
                            if entry_time:
                                time_display = entry_time.strftime("%m/%d %H:%M:%S")
                            else:
                                time_display = "N/A"
                    else:
                        time_display = "N/A"
                    
                    # Determine status
                    if target_pos and my_pos:
                        if target_pos['side'] == my_pos['side']:
                            status = "[green]✓ SYNC[/green]"
                        else:
                            status = "[red]⚠ DIFF[/red]"
                    elif target_pos and not my_pos:
                        status = "[yellow]⏳ COPY[/yellow]"
                    elif not target_pos and my_pos:
                        status = "[yellow]⏳ CLOSE[/yellow]"
                    else:
                        status = "-"
                    
                    # Color code sides
                    target_side_colored = f"[green]{target_side}[/green]" if target_side == "LONG" else f"[red]{target_side}[/red]" if target_side == "SHORT" else target_side
                    my_side_colored = f"[green]{my_side}[/green]" if my_side == "LONG" else f"[red]{my_side}[/red]" if my_side == "SHORT" else my_side
                    
                    table.add_row(
                        coin,
                        target_side_colored,
                        target_size,
                        my_side_colored,
                        my_size,
                        my_lev,
                        pnl_display,
                        time_display,
                        status
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
        
        with self.lock:
            recent_changes = list(self.position_changes)[-20:]  # Last 20 changes
            
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
        table.add_column("Margin", justify="right", width=10)
        
        with self.lock:
            recent_copies = list(self.executed_copies)[-15:]  # Last 15
            
            if not recent_copies:
                table.add_row("No copies yet", "", "", "", "", "", "")
            else:
                for copy in recent_copies:
                    side_style = "green" if copy['side'] == "long" else "red"
                    
                    table.add_row(
                        copy['time'].strftime("%Y-%m-%d %H:%M:%S"),
                        copy['coin'],
                        f"[{side_style}]{copy['side'].upper()}[/{side_style}]",
                        f"{copy['size']:.4f}",
                        f"{copy['leverage']:.1f}x",
                        f"${copy['entry_price']:,.4f}",
                        f"${copy.get('margin_used', 0):,.2f}"
                    )
        
        return table
    
    def create_stats_panel(self):
        """Create statistics panel."""
        with self.lock:
            # Calculate total PNL from your copied positions
            total_pnl = sum(pos.get('unrealized_pnl', 0) for pos in self.my_positions.values())
            pnl_style = "bold green" if total_pnl >= 0 else "bold red"
            pnl_sign = "-" if total_pnl < 0 else ""
            
            stats_text = (
                f"📊 Session Stats:\n"
                f"Positions Copied: [green]{self.stats['positions_copied']}[/green] | "
                f"Positions Closed: [yellow]{self.stats['positions_closed']}[/yellow] | "
                f"Total Volume: [green]${self.stats['total_volume']:,.2f}[/green] | "
                f"Your Total PNL: [{pnl_style}]${pnl_sign}{abs(total_pnl):,.2f}[/{pnl_style}] | "
                f"Skipped (Low Funds): [yellow]{self.stats['skipped_insufficient_funds']}[/yellow] | "
                f"Errors: [red]{self.stats['errors']}[/red]"
            )
        
        return Panel(stats_text, border_style="blue", box=ROUNDED)
    
    def create_layout(self):
        """Create the full dashboard layout."""
        from rich.columns import Columns
        
        layout = Layout()
        
        # Create two-column layout for positions tables
        positions_columns = Columns([
            self.create_all_target_positions_table(),
            self.create_copied_positions_table()
        ])
        
        layout.split_column(
            Layout(self.create_header(), name="header", size=6),
            Layout(positions_columns, name="positions"),
            Layout(self.create_history_table(), name="recent_copies", size=12),
            Layout(self.create_position_changes_table(), name="position_changes", size=12),
            Layout(self.create_stats_panel(), name="stats", size=4)
        )
        
        return layout
    
    def show_initial_state(self):
        """Display your current balance and positions before starting."""
        self.console.print("\n[bold cyan]═══════════════════════════════════════════════════════════[/bold cyan]")
        self.console.print("[bold cyan]           YOUR CURRENT ACCOUNT STATUS                      [/bold cyan]")
        self.console.print("[bold cyan]═══════════════════════════════════════════════════════════[/bold cyan]")
        
        # Get your current positions
        my_positions = self.get_positions(self.my_address)
        
        # Show balance info
        self.console.print(f"\n[cyan]Account Value:[/cyan] [bold green]${self.my_account_value:,.2f}[/bold green]")
        self.console.print(f"[cyan]Available Margin:[/cyan] [bold green]${self.my_available_margin:,.2f}[/bold green]")
        
        # Show current positions if any
        if my_positions:
            self.console.print(f"\n[yellow]⚠️  You currently have {len(my_positions)} open position(s):[/yellow]\n")
            
            # Create a table for current positions
            from rich.table import Table
            pos_table = Table(show_header=True, header_style="bold cyan", box=ROUNDED)
            pos_table.add_column("Coin", style="bold yellow", width=8)
            pos_table.add_column("Side", width=8)
            pos_table.add_column("Size", justify="right", width=14)
            pos_table.add_column("Entry", justify="right", width=12)
            pos_table.add_column("Leverage", justify="right", width=8)
            pos_table.add_column("Notional", justify="right", width=13)
            pos_table.add_column("PNL", justify="right", width=13)
            
            total_pnl = 0
            for coin, pos in sorted(my_positions.items()):
                side_style = "green" if pos['side'] == "long" else "red"
                pnl = pos.get('unrealized_pnl', 0)
                total_pnl += pnl
                pnl_style = "bold green" if pnl >= 0 else "bold red"
                pnl_sign = "-" if pnl < 0 else ""
                
                pos_table.add_row(
                    coin,
                    f"[{side_style}]{pos['side'].upper()}[/{side_style}]",
                    f"{pos['size']:.4f}",
                    f"${pos['entry_price']:,.2f}",
                    f"{pos['leverage']:.1f}x",
                    f"${pos['notional']:,.2f}",
                    f"[{pnl_style}]${pnl_sign}{abs(pnl):,.2f}[/{pnl_style}]"
                )
            
            self.console.print(pos_table)
            
            pnl_style = "bold green" if total_pnl >= 0 else "bold red"
            pnl_sign = "-" if total_pnl < 0 else ""
            self.console.print(f"\n[cyan]Total Unrealized PNL:[/cyan] [{pnl_style}]${pnl_sign}{abs(total_pnl):,.2f}[/{pnl_style}]")
        else:
            self.console.print(f"\n[green]✓ No open positions - starting with clean slate[/green]")
        
        self.console.print("\n[bold cyan]═══════════════════════════════════════════════════════════[/bold cyan]\n")
    
    def run(self, refresh_interval: int = 10):
        """Run the position copier with live dashboard."""
        self.console.print(f"[bold cyan]Starting Position Copier...[/bold cyan]")
        self.console.print(f"[bold yellow]Mode: Only copy NEW positions (ignoring existing ones)[/bold yellow]\n")
        
        # Initial account value check
        self.update_account_values()
        
        # Show your current account status
        self.show_initial_state()
        
        self.console.print(f"[yellow]Monitoring: {self.target_address} (${self.target_account_value:,.2f})[/yellow]")
        self.console.print(f"[yellow]Your Address: {self.my_address} (${self.my_account_value:,.2f})[/yellow]")
        self.console.print(f"[yellow]Copy Mode: {self.copy_mode}[/yellow]")
        
        # Show ratio calculation once at start
        if self.auto_calculate_ratio:
            self.console.print(f"[yellow]Copy Ratio: {self.copy_ratio:.4f} ({self.copy_ratio*100:.2f}%) [cyan](AUTO-CALCULATED)[/cyan][/yellow]")
        else:
            self.console.print(f"[yellow]Copy Ratio: {self.copy_ratio:.4f} ({self.copy_ratio*100:.2f}%)[/yellow]")
        
        self.console.print(f"[yellow]Max Per Position: {self.max_position_pct:.0f}% of your balance[/yellow]")
        self.console.print(f"[yellow]Margin Mode: [bold green]ISOLATED[/bold green] (safer - limits risk per position)[/yellow]")
        self.console.print(f"[yellow]Refresh Interval: {refresh_interval} seconds[/yellow]")
        
        # Warning if balance is low
        if self.my_account_value < 50:
            self.console.print(f"[red]⚠️  WARNING: Low balance (${self.my_account_value:.2f}). Consider depositing more for better copy execution.[/red]")
        
        self.console.print(f"\n[cyan]Taking initial snapshot of target's positions...[/cyan]")
        self.console.print(f"[green]Press Ctrl+C to stop[/green]\n")
        time.sleep(2)
        
        self.running = True
        
        # Initial sync - takes snapshot but doesn't copy
        self.sync_positions()
        time.sleep(1)
        
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
    
    # Validate required environment variables
    if not AGENT_PRIVATE_KEY:
        console.print("[red]❌ Error: AGENT_PRIVATE_KEY environment variable not set[/red]")
        console.print("[yellow]Please set it in your .env file or export it:[/yellow]")
        console.print("[yellow]  export AGENT_PRIVATE_KEY='0xyour_private_key'[/yellow]")
        return
    
    if not MAIN_ACCOUNT_ADDRESS:
        console.print("[red]❌ Error: MAIN_ACCOUNT_ADDRESS environment variable not set[/red]")
        console.print("[yellow]Please set it in your .env file or export it:[/yellow]")
        console.print("[yellow]  export MAIN_ACCOUNT_ADDRESS='0xyour_address'[/yellow]")
        return
    
    if not TARGET_ADDRESS:
        console.print("[red]❌ Error: TARGET_ADDRESS environment variable not set[/red]")
        console.print("[yellow]Please set it in your .env file or export it:[/yellow]")
        console.print("[yellow]  export TARGET_ADDRESS='0xtarget_address'[/yellow]")
        return
    
    # Use environment variables
    target_address = TARGET_ADDRESS
    console.print(f"[cyan]Target Address:[/cyan] {target_address}")
    
    # Use credentials from environment
    private_key = AGENT_PRIVATE_KEY
    main_account = MAIN_ACCOUNT_ADDRESS
    console.print("\n[green]✓ Using agent wallet for trading[/green]")
    console.print(f"[green]✓ Main account: {main_account[:8]}...{main_account[-6:]}[/green]")
    
    # Always use Proportional Auto mode
    copy_mode = "proportional"
    copy_ratio = 1.0  # Will be auto-calculated
    auto_calculate_ratio = True
    console.print("\n[cyan]✓ Using Proportional Auto mode (ratio auto-calculated from account balances)[/cyan]")
    
    # Hardcoded max position percentage
    max_position_pct = 75.0
    console.print("[cyan]✓ Max per position: 75% of balance[/cyan]")
    
    # Use refresh interval from environment variable
    refresh_interval = REFRESH_INTERVAL
    console.print(f"[cyan]✓ Refresh interval: {refresh_interval} seconds[/cyan]")
    
    # Show summary (no confirmation needed)
    console.print("\n[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print(f"[cyan]Target:[/cyan] {target_address}")
    console.print(f"[cyan]Mode:[/cyan] Proportional Auto")
    console.print(f"[cyan]Max Per Position:[/cyan] {max_position_pct:.0f}%")
    console.print(f"[cyan]Refresh Interval:[/cyan] {refresh_interval}s")
    console.print("[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print("[green]Starting in 2 seconds...[/green]\n")
    time.sleep(2)
    
    # Create and run copier
    try:
        copier = PositionCopier(
            target_address, 
            private_key,
            main_account_address=main_account,
            copy_mode=copy_mode, 
            copy_ratio=copy_ratio,
            max_position_pct=max_position_pct,
            auto_calculate_ratio=auto_calculate_ratio
        )
        copier.run(refresh_interval)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

