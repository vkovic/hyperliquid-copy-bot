#!/usr/bin/env python3
"""
Hyperliquid API Rate Limit Checker

This script helps you:
1. Monitor your API call frequency in real-time (across multiple processes)
2. Detect when you hit rate limits (429 errors or other rate limit indicators)
3. Track response times to identify API slowdowns
4. Test different API endpoints to understand rate limit thresholds
5. Provide statistics on your API usage

Usage:
    python rate_limit_checker.py              # Run passive monitor
    python rate_limit_checker.py --test       # Run stress test
    python rate_limit_checker.py --analyze    # Analyze existing usage patterns

To track API calls from other scripts, add this to your script:
    from rate_limit_checker import log_api_call
    
    # Before API call
    start = time.time()
    try:
        result = info.user_state(address)
        log_api_call('user_state', time.time() - start, 200)
    except Exception as e:
        log_api_call('user_state', time.time() - start, None, str(e))
"""

import time
import json
import threading
import requests
import os
import fcntl
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import Dict, List, Tuple
import argparse

from hyperliquid.info import Info
from hyperliquid.utils import constants

# Rich imports for dashboard
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from rich.layout import Layout
from rich.text import Text
from rich.box import ROUNDED

# Shared log file for cross-process tracking
LOG_FILE = "/tmp/hyperliquid_api_calls.jsonl"


def log_api_call(endpoint: str, duration: float, status_code: int = None, error: str = None):
    """
    Log an API call to shared file for cross-process monitoring.
    This function is thread-safe and can be called from multiple processes.
    
    Args:
        endpoint: Name of the API endpoint (e.g., 'user_state', 'meta')
        duration: Time taken in seconds
        status_code: HTTP status code (200, 429, etc.)
        error: Error message if call failed
    """
    log_entry = {
        'timestamp': time.time(),
        'endpoint': endpoint,
        'duration': duration,
        'status_code': status_code,
        'error': error,
        'pid': os.getpid()
    }
    
    try:
        # Use file locking to prevent race conditions
        with open(LOG_FILE, 'a') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(log_entry) + '\n')
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        # Silently fail - don't break the main script
        pass


def track_api_call(endpoint_name: str):
    """
    Decorator to automatically track and log API calls.
    
    Usage:
        @track_api_call('user_state')
        def get_user_data(address):
            return info.user_state(address)
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            error = None
            status_code = None
            
            try:
                result = func(*args, **kwargs)
                status_code = 200
                return result
            except Exception as e:
                error = str(e)
                if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                    status_code = e.response.status_code
                raise
            finally:
                duration = time.time() - start_time
                log_api_call(endpoint_name, duration, status_code, error)
        
        return wrapper
    return decorator


def read_api_log(max_age_seconds: int = 300) -> List[Dict]:
    """
    Read API calls from shared log file.
    
    Args:
        max_age_seconds: Only return calls within this many seconds
        
    Returns:
        List of API call dictionaries
    """
    if not os.path.exists(LOG_FILE):
        return []
    
    calls = []
    cutoff_time = time.time() - max_age_seconds
    
    try:
        with open(LOG_FILE, 'r') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            for line in f:
                try:
                    call = json.loads(line.strip())
                    if call['timestamp'] >= cutoff_time:
                        calls.append(call)
                except json.JSONDecodeError:
                    continue
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    
    return calls


def clear_old_logs(max_age_seconds: int = 3600):
    """Clear log entries older than max_age_seconds."""
    if not os.path.exists(LOG_FILE):
        return
    
    try:
        recent_calls = read_api_log(max_age_seconds)
        with open(LOG_FILE, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            for call in recent_calls:
                f.write(json.dumps(call) + '\n')
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

# Rate limit tracking
class RateLimitTracker:
    def __init__(self, window_seconds=60, read_from_log=False):
        self.window_seconds = window_seconds
        self.read_from_log = read_from_log  # Whether to read from shared log file
        self.api_calls = deque()  # (timestamp, endpoint, duration, status_code, error)
        self.rate_limit_events = []  # Record when we hit rate limits
        self.lock = threading.Lock()
        self.processed_timestamps = set()  # Track which log entries we've already processed
        self.stats = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'rate_limit_hits': 0,
            'avg_response_time': 0,
            'max_response_time': 0,
            'min_response_time': float('inf'),
            'calls_per_minute': 0,
            'session_start': time.time(),
            'active_processes': set()
        }
    
    def record_call(self, endpoint: str, duration: float, status_code: int = None, error: str = None):
        """Record an API call with its details."""
        current_time = time.time()
        
        with self.lock:
            # Add to deque
            self.api_calls.append({
                'timestamp': current_time,
                'endpoint': endpoint,
                'duration': duration,
                'status_code': status_code,
                'error': error,
                'is_rate_limit': self._is_rate_limit_error(status_code, error)
            })
            
            # Update stats
            self.stats['total_calls'] += 1
            
            if status_code and 200 <= status_code < 300:
                self.stats['successful_calls'] += 1
            else:
                self.stats['failed_calls'] += 1
            
            if self._is_rate_limit_error(status_code, error):
                self.stats['rate_limit_hits'] += 1
                self.rate_limit_events.append({
                    'timestamp': current_time,
                    'endpoint': endpoint,
                    'error': error
                })
            
            # Update response time stats
            if duration > 0:
                self.stats['max_response_time'] = max(self.stats['max_response_time'], duration)
                self.stats['min_response_time'] = min(self.stats['min_response_time'], duration)
                
                # Calculate rolling average
                recent_calls = [c for c in self.api_calls if c['duration'] > 0]
                if recent_calls:
                    self.stats['avg_response_time'] = sum(c['duration'] for c in recent_calls) / len(recent_calls)
            
            # Clean up old entries outside the window
            cutoff_time = current_time - self.window_seconds
            while self.api_calls and self.api_calls[0]['timestamp'] < cutoff_time:
                self.api_calls.popleft()
            
            # Calculate calls per minute
            calls_in_window = len(self.api_calls)
            self.stats['calls_per_minute'] = (calls_in_window / self.window_seconds) * 60
    
    def _is_rate_limit_error(self, status_code: int, error: str) -> bool:
        """Determine if an error is a rate limit error."""
        # HTTP 429 is the standard rate limit status
        if status_code == 429:
            return True
        
        # Check error messages for rate limit indicators
        if error:
            rate_limit_keywords = [
                'rate limit',
                'too many requests',
                'throttle',
                'quota exceeded',
                'limit exceeded'
            ]
            error_lower = error.lower()
            return any(keyword in error_lower for keyword in rate_limit_keywords)
        
        return False
    
    def get_stats(self) -> Dict:
        """Get current statistics."""
        with self.lock:
            return self.stats.copy()
    
    def get_recent_calls(self, limit: int = 20) -> List[Dict]:
        """Get recent API calls."""
        with self.lock:
            recent = list(self.api_calls)[-limit:]
            return recent
    
    def get_calls_by_endpoint(self) -> Dict[str, int]:
        """Get call counts grouped by endpoint."""
        with self.lock:
            endpoint_counts = defaultdict(int)
            for call in self.api_calls:
                endpoint_counts[call['endpoint']] += 1
            return dict(endpoint_counts)
    
    def get_rate_limit_events(self) -> List[Dict]:
        """Get all rate limit events."""
        with self.lock:
            return self.rate_limit_events.copy()
    
    def load_from_log_file(self):
        """Load API calls from shared log file (for cross-process monitoring)."""
        if not self.read_from_log:
            return
        
        log_calls = read_api_log(self.window_seconds)
        
        with self.lock:
            for call in log_calls:
                # Create unique ID to avoid processing same entry twice
                call_id = (call['timestamp'], call['endpoint'], call.get('pid', 0))
                
                if call_id in self.processed_timestamps:
                    continue
                
                self.processed_timestamps.add(call_id)
                
                # Add to deque
                self.api_calls.append({
                    'timestamp': call['timestamp'],
                    'endpoint': call['endpoint'],
                    'duration': call['duration'],
                    'status_code': call.get('status_code'),
                    'error': call.get('error'),
                    'is_rate_limit': self._is_rate_limit_error(call.get('status_code'), call.get('error')),
                    'pid': call.get('pid')
                })
                
                # Track active processes
                if 'pid' in call:
                    self.stats['active_processes'].add(call['pid'])
                
                # Update stats
                self.stats['total_calls'] += 1
                
                if call.get('status_code') and 200 <= call['status_code'] < 300:
                    self.stats['successful_calls'] += 1
                else:
                    self.stats['failed_calls'] += 1
                
                if self._is_rate_limit_error(call.get('status_code'), call.get('error')):
                    self.stats['rate_limit_hits'] += 1
                    self.rate_limit_events.append({
                        'timestamp': call['timestamp'],
                        'endpoint': call['endpoint'],
                        'error': call.get('error')
                    })
                
                # Update response time stats
                duration = call.get('duration', 0)
                if duration > 0:
                    self.stats['max_response_time'] = max(self.stats['max_response_time'], duration)
                    self.stats['min_response_time'] = min(self.stats['min_response_time'], duration)
            
            # Recalculate average and calls per minute
            if self.api_calls:
                durations = [c['duration'] for c in self.api_calls if c['duration'] > 0]
                if durations:
                    self.stats['avg_response_time'] = sum(durations) / len(durations)
                
                calls_in_window = len(self.api_calls)
                self.stats['calls_per_minute'] = (calls_in_window / self.window_seconds) * 60
            
            # Clean up old processed timestamps
            cutoff_time = time.time() - self.window_seconds
            self.processed_timestamps = {
                ts for ts in self.processed_timestamps 
                if ts[0] >= cutoff_time
            }


# Wrapper for Hyperliquid Info class with rate limit tracking
class TrackedInfo(Info):
    def __init__(self, *args, tracker: RateLimitTracker = None, **kwargs):
        # Set tracker BEFORE calling super().__init__() because parent init calls meta()
        self.tracker = tracker or RateLimitTracker()
        super().__init__(*args, **kwargs)
    
    def _tracked_call(self, method_name: str, original_method, *args, **kwargs):
        """Wrapper to track API calls."""
        start_time = time.time()
        error = None
        status_code = None
        
        try:
            result = original_method(*args, **kwargs)
            status_code = 200  # Assume success if no exception
            return result
        except requests.exceptions.HTTPError as e:
            error = str(e)
            status_code = e.response.status_code if hasattr(e, 'response') else None
            raise
        except Exception as e:
            error = str(e)
            raise
        finally:
            duration = time.time() - start_time
            self.tracker.record_call(method_name, duration, status_code, error)
            # Also log to shared file for cross-process monitoring
            log_api_call(method_name, duration, status_code, error)
    
    # Override common methods to track them
    def user_state(self, *args, **kwargs):
        return self._tracked_call('user_state', super().user_state, *args, **kwargs)
    
    def meta(self, *args, **kwargs):
        return self._tracked_call('meta', super().meta, *args, **kwargs)
    
    def all_mids(self, *args, **kwargs):
        return self._tracked_call('all_mids', super().all_mids, *args, **kwargs)
    
    def user_fills(self, *args, **kwargs):
        return self._tracked_call('user_fills', super().user_fills, *args, **kwargs)
    
    def funding_history(self, *args, **kwargs):
        return self._tracked_call('funding_history', super().funding_history, *args, **kwargs)


# Dashboard for displaying rate limit information
class RateLimitDashboard:
    def __init__(self, tracker: RateLimitTracker):
        self.tracker = tracker
        self.console = Console()
    
    def create_layout(self):
        """Create the dashboard layout."""
        stats = self.tracker.get_stats()
        
        # Header
        session_time = int(time.time() - stats['session_start'])
        header_text = "🔍 HYPERLIQUID API RATE LIMIT MONITOR"
        session_info = f"Session: {session_time//3600:02d}:{(session_time//60)%60:02d}:{session_time%60:02d}"
        
        header_panel = Panel(
            f"[bold cyan]{header_text}[/bold cyan]\n{session_info}",
            border_style="cyan",
            box=ROUNDED
        )
        
        # Statistics Panel
        rate_limit_status = "[bold red]⚠️ RATE LIMITED[/bold red]" if stats['rate_limit_hits'] > 0 else "[bold green]✓ Normal[/bold green]"
        
        stats_table = Table(show_header=False, box=None, padding=(0, 2))
        stats_table.add_column(style="cyan", justify="right")
        stats_table.add_column(style="white")
        
        stats_table.add_row("Status:", rate_limit_status)
        stats_table.add_row("Active Processes:", f"{len(stats.get('active_processes', set()))}")
        stats_table.add_row("Total API Calls:", f"{stats['total_calls']:,}")
        stats_table.add_row("Successful:", f"[green]{stats['successful_calls']:,}[/green]")
        stats_table.add_row("Failed:", f"[red]{stats['failed_calls']:,}[/red]")
        stats_table.add_row("Rate Limit Hits:", f"[bold red]{stats['rate_limit_hits']:,}[/bold red]")
        stats_table.add_row("Calls/Minute:", f"{stats['calls_per_minute']:.1f}")
        stats_table.add_row("Avg Response:", f"{stats['avg_response_time']*1000:.1f}ms")
        stats_table.add_row("Max Response:", f"{stats['max_response_time']*1000:.1f}ms")
        if stats['min_response_time'] != float('inf'):
            stats_table.add_row("Min Response:", f"{stats['min_response_time']*1000:.1f}ms")
        
        stats_panel = Panel(stats_table, title="📊 Statistics", border_style="blue", box=ROUNDED)
        
        # Endpoints Table
        endpoints_table = Table(
            title="🎯 Calls by Endpoint",
            show_header=True,
            header_style="bold yellow",
            border_style="yellow",
            box=ROUNDED
        )
        endpoints_table.add_column("Endpoint", style="cyan")
        endpoints_table.add_column("Count", justify="right", style="white")
        endpoints_table.add_column("% of Total", justify="right", style="green")
        
        endpoint_counts = self.tracker.get_calls_by_endpoint()
        total_calls = sum(endpoint_counts.values())
        
        for endpoint, count in sorted(endpoint_counts.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total_calls * 100) if total_calls > 0 else 0
            endpoints_table.add_row(endpoint, f"{count:,}", f"{percentage:.1f}%")
        
        # Recent Calls Table
        recent_calls_table = Table(
            title="📝 Recent API Calls",
            show_header=True,
            header_style="bold magenta",
            border_style="magenta",
            box=ROUNDED
        )
        recent_calls_table.add_column("Time", style="cyan", width=8)
        recent_calls_table.add_column("Endpoint", style="yellow", width=20)
        recent_calls_table.add_column("Duration", justify="right", width=10)
        recent_calls_table.add_column("Status", justify="center", width=10)
        
        for call in self.tracker.get_recent_calls(15):
            call_time = datetime.fromtimestamp(call['timestamp']).strftime("%H:%M:%S")
            duration_ms = f"{call['duration']*1000:.0f}ms"
            
            # Determine status display
            if call['is_rate_limit']:
                status = "[bold red]RATE LIMIT[/bold red]"
            elif call['status_code'] and 200 <= call['status_code'] < 300:
                status = f"[green]{call['status_code']}[/green]"
            elif call['status_code']:
                status = f"[red]{call['status_code']}[/red]"
            else:
                status = "[yellow]ERROR[/yellow]"
            
            # Color code duration
            duration_colored = duration_ms
            if call['duration'] > 2:
                duration_colored = f"[red]{duration_ms}[/red]"
            elif call['duration'] > 1:
                duration_colored = f"[yellow]{duration_ms}[/yellow]"
            else:
                duration_colored = f"[green]{duration_ms}[/green]"
            
            recent_calls_table.add_row(call_time, call['endpoint'], duration_colored, status)
        
        # Rate Limit Events Table
        rate_limit_events = self.tracker.get_rate_limit_events()
        if rate_limit_events:
            events_table = Table(
                title="⚠️ Rate Limit Events",
                show_header=True,
                header_style="bold red",
                border_style="red",
                box=ROUNDED
            )
            events_table.add_column("Time", style="cyan")
            events_table.add_column("Endpoint", style="yellow")
            events_table.add_column("Error", style="red")
            
            for event in rate_limit_events[-10:]:  # Show last 10
                event_time = datetime.fromtimestamp(event['timestamp']).strftime("%H:%M:%S")
                error = event['error'][:50] + "..." if event['error'] and len(event['error']) > 50 else event['error'] or "N/A"
                events_table.add_row(event_time, event['endpoint'], error)
        
        # Create layout
        layout = Layout()
        
        if rate_limit_events:
            layout.split_column(
                Layout(header_panel, name="header", size=3),
                Layout(stats_panel, name="stats", size=12),
                Layout(endpoints_table, name="endpoints"),
                Layout(recent_calls_table, name="recent"),
                Layout(events_table, name="events", size=8)
            )
        else:
            layout.split_column(
                Layout(header_panel, name="header", size=3),
                Layout(stats_panel, name="stats", size=12),
                Layout(endpoints_table, name="endpoints"),
                Layout(recent_calls_table, name="recent")
            )
        
        return layout


def run_passive_monitor():
    """Run passive monitoring - tracks API calls in real-time from all processes."""
    console = Console()
    console.print("[cyan]Starting Cross-Process Rate Limit Monitor...[/cyan]")
    console.print("[yellow]This will track API calls from all running scripts.[/yellow]")
    console.print(f"[yellow]Log file: {LOG_FILE}[/yellow]\n")
    
    # Clear old log entries on startup
    clear_old_logs(max_age_seconds=300)
    
    # Enable log reading for cross-process monitoring
    tracker = RateLimitTracker(window_seconds=60, read_from_log=True)
    dashboard = RateLimitDashboard(tracker)
    
    # Make a few test calls to initialize
    console.print("[cyan]Initializing with test API calls...[/cyan]")
    info = TrackedInfo(constants.MAINNET_API_URL, skip_ws=True, tracker=tracker)
    
    try:
        info.meta()
        info.all_mids()
        console.print("[green]✓ Initialization complete[/green]\n")
    except Exception as e:
        console.print(f"[red]Error during initialization: {e}[/red]\n")
    
    console.print("[cyan]Monitoring API calls from all processes...[/cyan]")
    console.print("[cyan]Press Ctrl+C to stop[/cyan]\n")
    time.sleep(1)
    
    # Background thread to periodically load from log file
    stop_loading = threading.Event()
    
    def log_loader():
        while not stop_loading.is_set():
            try:
                tracker.load_from_log_file()
            except Exception as e:
                pass  # Silently continue
            time.sleep(1)  # Check log file every second
    
    loader_thread = threading.Thread(target=log_loader, daemon=True)
    loader_thread.start()
    
    # Start live dashboard
    with Live(dashboard.create_layout(), refresh_per_second=2, screen=True) as live:
        try:
            while True:
                live.update(dashboard.create_layout())
                time.sleep(0.5)
        except KeyboardInterrupt:
            console.print("\n[red]🛑 Stopping monitor...[/red]")
            stop_loading.set()


def run_stress_test():
    """Run stress test to intentionally hit rate limits."""
    console = Console()
    console.print("[bold yellow]⚠️  STRESS TEST MODE[/bold yellow]")
    console.print("[yellow]This will make rapid API calls to test rate limits.[/yellow]\n")
    
    # Ask for confirmation
    response = input("Continue? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        console.print("[red]Stress test cancelled.[/red]")
        return
    
    tracker = RateLimitTracker(window_seconds=60)
    dashboard = RateLimitDashboard(tracker)
    info = TrackedInfo(constants.MAINNET_API_URL, skip_ws=True, tracker=tracker)
    
    # Test parameters
    test_delays = [0.1, 0.05, 0.01]  # Seconds between requests
    calls_per_delay = 20
    
    console.print(f"\n[cyan]Starting stress test with {len(test_delays)} phases...[/cyan]\n")
    
    stop_event = threading.Event()
    
    def stress_test_thread():
        """Run stress test in background."""
        for i, delay in enumerate(test_delays):
            if stop_event.is_set():
                break
            
            console.print(f"[yellow]Phase {i+1}: {1/delay:.1f} calls/sec (delay={delay}s)[/yellow]")
            
            for j in range(calls_per_delay):
                if stop_event.is_set():
                    break
                
                try:
                    # Alternate between different endpoints
                    if j % 3 == 0:
                        info.all_mids()
                    elif j % 3 == 1:
                        info.meta()
                    else:
                        info.funding_history("BTC", startTime=int((time.time() - 86400) * 1000))
                    
                    time.sleep(delay)
                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")
                    time.sleep(1)
            
            # Pause between phases
            if not stop_event.is_set():
                time.sleep(2)
        
        console.print("\n[green]Stress test complete![/green]")
        time.sleep(2)
        stop_event.set()
    
    # Start stress test in background
    test_thread = threading.Thread(target=stress_test_thread, daemon=True)
    test_thread.start()
    
    # Show live dashboard
    with Live(dashboard.create_layout(), refresh_per_second=2, screen=True) as live:
        try:
            while not stop_event.is_set():
                live.update(dashboard.create_layout())
                time.sleep(0.5)
        except KeyboardInterrupt:
            console.print("\n[red]🛑 Stopping stress test...[/red]")
            stop_event.set()
    
    # Wait for thread to finish
    test_thread.join(timeout=5)
    
    # Print summary
    stats = tracker.get_stats()
    console.print("\n[bold cyan]📊 Test Summary:[/bold cyan]")
    console.print(f"Total Calls: {stats['total_calls']}")
    console.print(f"Successful: [green]{stats['successful_calls']}[/green]")
    console.print(f"Failed: [red]{stats['failed_calls']}[/red]")
    console.print(f"Rate Limit Hits: [bold red]{stats['rate_limit_hits']}[/bold red]")
    console.print(f"Peak Calls/Minute: {stats['calls_per_minute']:.1f}")
    console.print(f"Avg Response Time: {stats['avg_response_time']*1000:.1f}ms")


def analyze_patterns():
    """Analyze usage patterns and provide recommendations."""
    console = Console()
    console.print("[cyan]API Usage Pattern Analyzer[/cyan]\n")
    
    tracker = RateLimitTracker(window_seconds=60)
    info = TrackedInfo(constants.MAINNET_API_URL, skip_ws=True, tracker=tracker)
    
    # Simulate typical usage patterns
    test_scenarios = [
        ("Single user_state call", lambda: info.user_state("0x0000000000000000000000000000000000000000")),
        ("Single meta call", lambda: info.meta()),
        ("Single all_mids call", lambda: info.all_mids()),
    ]
    
    console.print("[yellow]Testing typical API call patterns...[/yellow]\n")
    
    results = []
    for name, test_func in test_scenarios:
        times = []
        for i in range(5):
            start = time.time()
            try:
                test_func()
                duration = time.time() - start
                times.append(duration)
            except Exception as e:
                console.print(f"[red]Error in {name}: {e}[/red]")
            time.sleep(0.5)  # Delay between tests
        
        if times:
            avg_time = sum(times) / len(times)
            results.append((name, avg_time))
            console.print(f"✓ {name}: {avg_time*1000:.1f}ms average")
    
    # Recommendations
    console.print("\n[bold cyan]📋 Recommendations:[/bold cyan]\n")
    
    stats = tracker.get_stats()
    
    if stats['rate_limit_hits'] > 0:
        console.print("[red]⚠️  You have hit rate limits![/red]")
        console.print("[yellow]Recommendations:[/yellow]")
        console.print("  • Add delays between API calls (0.1-0.2s minimum)")
        console.print("  • Implement caching for frequently accessed data")
        console.print("  • Use WebSocket subscriptions instead of polling")
    else:
        console.print("[green]✓ No rate limits detected in testing[/green]")
        console.print(f"[cyan]Safe call rate: ~{stats['calls_per_minute']:.0f} calls/minute[/cyan]")
        console.print("\n[yellow]Best practices:[/yellow]")
        console.print("  • Keep calls under 10-20 per minute for safety")
        console.print("  • Cache user_state data (60s expiry recommended)")
        console.print("  • Use WebSockets for real-time data")
        console.print("  • Implement exponential backoff on errors")
    
    console.print("\n[cyan]Endpoint Performance:[/cyan]")
    for name, avg_time in results:
        console.print(f"  • {name}: {avg_time*1000:.0f}ms")


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid API Rate Limit Checker")
    parser.add_argument('--test', action='store_true', help="Run stress test to find rate limits")
    parser.add_argument('--analyze', action='store_true', help="Analyze usage patterns and provide recommendations")
    args = parser.parse_args()
    
    if args.test:
        run_stress_test()
    elif args.analyze:
        analyze_patterns()
    else:
        run_passive_monitor()


if __name__ == "__main__":
    main()

