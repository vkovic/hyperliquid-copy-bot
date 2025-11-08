#!/usr/bin/env python3
"""
Example: How to integrate API call tracking into your existing scripts.

This shows two approaches:
1. Simple wrapper function (easiest)
2. Using the TrackedInfo class (automatic tracking)
"""

import time
from hyperliquid.info import Info
from hyperliquid.utils import constants
from rate_limit_checker import log_api_call, TrackedInfo

# Approach 1: Manual logging with wrapper function
def example_manual_tracking():
    """Example using manual logging."""
    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    
    # Wrap API calls with timing and logging
    start = time.time()
    try:
        result = info.meta()
        log_api_call('meta', time.time() - start, 200)
        print(f"✓ meta() call logged: {len(result['universe'])} assets")
    except Exception as e:
        log_api_call('meta', time.time() - start, None, str(e))
        print(f"✗ meta() call failed: {e}")
    
    # Another example
    start = time.time()
    try:
        result = info.all_mids()
        log_api_call('all_mids', time.time() - start, 200)
        print(f"✓ all_mids() call logged: {len(result)} prices")
    except Exception as e:
        log_api_call('all_mids', time.time() - start, None, str(e))
        print(f"✗ all_mids() call failed: {e}")


# Approach 2: Using TrackedInfo (automatic tracking)
def example_automatic_tracking():
    """Example using TrackedInfo for automatic tracking."""
    # Replace Info with TrackedInfo - all calls are automatically tracked
    info = TrackedInfo(constants.MAINNET_API_URL, skip_ws=True)
    
    # These calls are automatically tracked and logged
    result = info.meta()
    print(f"✓ meta() call auto-tracked: {len(result['universe'])} assets")
    
    result = info.all_mids()
    print(f"✓ all_mids() call auto-tracked: {len(result)} prices")
    
    # user_state is also tracked
    try:
        result = info.user_state("0x0000000000000000000000000000000000000000")
        print(f"✓ user_state() call auto-tracked")
    except Exception as e:
        print(f"✗ user_state() failed (expected): {e}")


if __name__ == "__main__":
    print("=" * 60)
    print("Example 1: Manual Tracking")
    print("=" * 60)
    example_manual_tracking()
    
    print("\n" + "=" * 60)
    print("Example 2: Automatic Tracking with TrackedInfo")
    print("=" * 60)
    example_automatic_tracking()
    
    print("\n" + "=" * 60)
    print("Now run: python rate_limit_checker.py")
    print("to see these API calls in the monitoring dashboard!")
    print("=" * 60)

