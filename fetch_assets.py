#!/usr/bin/env python3
"""
Fetch and display assets for a Hyperliquid address.
Shows both spot holdings and futures positions.
"""

import os
import sys
from datetime import datetime
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Rich imports for beautiful output
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.box import ROUNDED

# Load .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Initialize Hyperliquid API
info = Info(constants.MAINNET_API_URL, skip_ws=True)
console = Console()


def fetch_spot_assets(address: str):
    """Fetch spot asset holdings for an address."""
    try:
        spot_state = info.spot_user_state(address)
        
        # Get spot metadata to map token names to indices
        name_to_index = {}
        try:
            spot_meta = info.spot_meta()
            if spot_meta and 'tokens' in spot_meta:
                for token in spot_meta['tokens']:
                    name = token['name']
                    index = token['index']
                    name_to_index[name] = index
        except Exception as e:
            console.print(f"[yellow]Could not fetch spot metadata: {e}[/yellow]")
        
        # Get spot prices from multiple sources
        spot_prices = {}
        
        # Source 1: all_mids() includes many spot tokens
        try:
            all_mids = info.all_mids()
            spot_prices.update(all_mids)
        except Exception as e:
            console.print(f"[yellow]Could not fetch all_mids: {e}[/yellow]")
        
        # Source 2: spot market trading pairs
        # Handles both "TOKEN/USDC" format and "@N" (index) format
        try:
            spot_meta_and_prices = info.spot_meta_and_asset_ctxs()
            if spot_meta_and_prices and len(spot_meta_and_prices) > 1:
                spot_meta_data = spot_meta_and_prices[0]
                asset_ctxs = spot_meta_and_prices[1]
                
                # Create index to name mapping for @N notation
                index_to_name = {}
                if spot_meta_data and 'tokens' in spot_meta_data:
                    for token in spot_meta_data['tokens']:
                        index_to_name[token['index']] = token['name']
                
                for ctx in asset_ctxs:
                    coin = ctx.get('coin', '')
                    mid_px = ctx.get('midPx') or ctx.get('markPx')
                    
                    if mid_px and coin:
                        # Handle @N notation (e.g., "@196" for JPEG)
                        if coin.startswith('@'):
                            try:
                                idx = int(coin.replace('@', ''))
                                token_name = index_to_name.get(idx)
                                # Only add if not already present (don't overwrite all_mids prices)
                                if token_name and token_name not in spot_prices:
                                    spot_prices[token_name] = float(mid_px)
                            except:
                                pass
                        # Handle TOKEN/USDC pairs
                        elif '/' in coin:
                            base_token = coin.split('/')[0]
                            # Only use USDC pairs for USD pricing
                            # Only add if not already present (don't overwrite all_mids prices)
                            if '/USDC' in coin and base_token not in spot_prices:
                                spot_prices[base_token] = float(mid_px)
        except Exception as e:
            console.print(f"[yellow]Could not fetch spot trading pairs: {e}[/yellow]")
        
        assets = []
        if spot_state and 'balances' in spot_state:
            for balance in spot_state['balances']:
                try:
                    coin = balance.get('coin', '')
                    total_str = balance.get('total', '0')
                    hold_str = balance.get('hold', '0')
                    
                    # Convert to float, handling both string and numeric types
                    total = float(total_str) if total_str else 0
                    hold = float(hold_str) if hold_str else 0
                    available = total - hold
                    
                    if total > 0:
                        # Calculate USD value
                        usd_value = 0
                        if coin in spot_prices:
                            price = float(spot_prices[coin])
                            usd_value = total * price
                        elif coin == 'USDC' or coin == 'USD':
                            # Stablecoins are 1:1 USD
                            usd_value = total
                        
                        assets.append({
                            'coin': coin,
                            'total': total,
                            'available': available,
                            'on_hold': hold,
                            'usd_value': usd_value
                        })
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not process balance for {balance.get('coin', 'unknown')}: {e}[/yellow]")
                    continue
        
        return assets
    
    except Exception as e:
        console.print(f"[red]Error fetching spot assets: {e}[/red]")
        return []


def fetch_futures_positions(address: str):
    """Fetch futures positions for an address."""
    try:
        user_state = info.user_state(address)
        
        positions = []
        account_value = 0
        total_pnl = 0
        
        if user_state:
            # Get account value
            if 'marginSummary' in user_state:
                account_value = float(user_state['marginSummary'].get('accountValue', 0))
            
            # Process positions
            if 'assetPositions' in user_state:
                # Get metadata for coin info
                meta = info.meta()
                universe = {asset['name']: asset for asset in meta['universe']}
                
                # Get current prices
                try:
                    all_mids = info.all_mids()
                except:
                    all_mids = {}
                
                for asset_pos in user_state['assetPositions']:
                    position = asset_pos.get('position', {})
                    coin = position.get('coin')
                    
                    if not coin:
                        continue
                    
                    # Get position size
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
                    mark_px = float(all_mids.get(coin, 0))
                    
                    # Leverage
                    leverage_info = position.get('leverage', {})
                    if isinstance(leverage_info, dict):
                        leverage = float(leverage_info.get('value', 0))
                        leverage_type = leverage_info.get('type', 'cross')
                    else:
                        leverage = position_value / margin_used if margin_used > 0 else 0
                        leverage_type = 'cross'
                    
                    # ROI
                    roi = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0
                    
                    # Side
                    side = "SHORT" if szi < 0 else "LONG"
                    
                    positions.append({
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
                    
                    total_pnl += unrealized_pnl
        
        return positions, account_value, total_pnl
    
    except Exception as e:
        console.print(f"[red]Error fetching futures positions: {e}[/red]")
        return [], 0, 0


def display_spot_assets(assets):
    """Display spot assets in a table."""
    if not assets:
        console.print("[yellow]No spot assets found[/yellow]\n")
        return
    
    # Calculate total USD value
    total_usd_value = sum(asset['usd_value'] for asset in assets)
    
    table = Table(
        title="💰 SPOT ASSETS",
        show_header=True,
        header_style="bold green",
        border_style="green",
        box=ROUNDED,
        caption=f"Total Value: [bold green]${total_usd_value:,.2f}[/bold green]" if total_usd_value > 0 else None
    )
    
    table.add_column("Asset", style="bold yellow", width=12)
    table.add_column("Total", justify="right", width=18)
    table.add_column("Available", justify="right", width=18)
    table.add_column("On Hold", justify="right", width=18)
    table.add_column("Value (USD)", justify="right", width=15, style="bold green")
    
    # Sort by USD value (highest first), then by coin name
    sorted_assets = sorted(assets, key=lambda x: (-x['usd_value'], x['coin']))
    
    for asset in sorted_assets:
        usd_display = f"${asset['usd_value']:,.2f}" if asset['usd_value'] > 0 else "[dim]N/A[/dim]"
        table.add_row(
            asset['coin'],
            f"{asset['total']:.8f}",
            f"{asset['available']:.8f}",
            f"{asset['on_hold']:.8f}",
            usd_display
        )
    
    console.print(table)
    console.print()


def display_futures_positions(positions, account_value, total_pnl):
    """Display futures positions in a table."""
    # Calculate total margin used in positions
    total_margin_used = sum(pos['margin'] for pos in positions)
    available_margin = account_value - total_margin_used
    
    # Display account summary
    pnl_style = "bold green" if total_pnl >= 0 else "bold red"
    summary = (
        f"Account Value: [bold green]${account_value:,.2f}[/bold green] | "
        f"Available Margin: [bold yellow]${available_margin:,.2f}[/bold yellow] | "
        f"Used Margin: [cyan]${total_margin_used:,.2f}[/cyan] | "
        f"Total Unrealized PNL: [{pnl_style}]${total_pnl:+,.2f}[/{pnl_style}] | "
        f"Open Positions: [cyan]{len(positions)}[/cyan]"
    )
    console.print(Panel(summary, border_style="cyan", box=ROUNDED, title="[bold cyan]FUTURES ACCOUNT[/bold cyan]"))
    console.print()
    
    if not positions:
        if account_value > 0:
            console.print("[yellow]No active futures positions[/yellow]")
            console.print(f"[cyan]💰 Available balance in futures account: [bold green]${available_margin:,.2f}[/bold green] (USDC)[/cyan]\n")
        else:
            console.print("[yellow]No futures positions found[/yellow]\n")
        return
    
    table = Table(
        title="🔮 FUTURES POSITIONS",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        box=ROUNDED
    )
    
    table.add_column("Coin", style="bold yellow", width=10)
    table.add_column("Side", width=8)
    table.add_column("Size", justify="right", width=14)
    table.add_column("Entry", justify="right", width=14)
    table.add_column("Mark", justify="right", width=14)
    table.add_column("Notional", justify="right", width=15)
    table.add_column("PNL", justify="right", width=15)
    table.add_column("ROI%", justify="right", width=12)
    table.add_column("Margin", justify="right", width=14)
    table.add_column("Lev", justify="right", width=8)
    table.add_column("Liq Price", justify="right", width=15)
    
    for pos in sorted(positions, key=lambda x: abs(x['unrealized_pnl']), reverse=True):
        # Color coding
        side_style = "bold green" if pos['side'] == "LONG" else "bold red"
        pnl_style = "bold green" if pos['unrealized_pnl'] >= 0 else "bold red"
        roi_style = "bold green" if pos['roi'] >= 0 else "bold red"
        lev_style = "bold red" if pos['leverage'] >= 20 else "bold yellow" if pos['leverage'] >= 10 else "white"
        
        # Format liquidation price
        if pos['liquidation_px']:
            liq_px_str = f"${float(pos['liquidation_px']):,.2f}"
        else:
            liq_px_str = "N/A"
        
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
    
    console.print(table)
    console.print()


def main():
    """Main function to fetch and display assets."""
    console.print()
    console.print(Panel(
        "[bold cyan]📊 HYPERLIQUID ASSET FETCHER[/bold cyan]",
        border_style="cyan",
        box=ROUNDED
    ))
    console.print()
    
    # Get target address from command line (required)
    if len(sys.argv) < 2:
        console.print("[red]❌ Error: Address required as first argument[/red]")
        console.print("[yellow]Usage:[/yellow]")
        console.print("[yellow]  python fetch_assets.py <address>[/yellow]")
        console.print("[yellow]Example:[/yellow]")
        console.print("[yellow]  python fetch_assets.py 0x42b9493c505adf4b37dda028b6b47f7b5e8a5d1f[/yellow]")
        console.print()
        sys.exit(1)
    
    target_address = sys.argv[1]
    
    # Validate address format (basic check)
    if not target_address.startswith("0x"):
        console.print(f"[yellow]Warning: Address format looks unusual (doesn't start with 0x)[/yellow]")
    
    console.print(f"[cyan]Target Address:[/cyan] [yellow]{target_address}[/yellow]")
    console.print(f"[cyan]Fetch Time:[/cyan] [white]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/white]")
    console.print()
    
    # Fetch data
    console.print("[cyan]Fetching data from Hyperliquid...[/cyan]")
    console.print()
    
    # Fetch spot assets
    spot_assets = fetch_spot_assets(target_address)
    display_spot_assets(spot_assets)
    
    # Fetch futures positions
    futures_positions, account_value, total_pnl = fetch_futures_positions(target_address)
    display_futures_positions(futures_positions, account_value, total_pnl)
    
    console.print("[green]✓ Fetch complete![/green]")
    console.print()


if __name__ == "__main__":
    main()

