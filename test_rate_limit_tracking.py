#!/usr/bin/env python3
"""
Quick test to verify rate limit tracking is working.
Run this, then check the rate_limit_checker.py dashboard.
"""

import time
from hyperliquid.info import Info
from hyperliquid.utils import constants
from rate_limit_checker import log_api_call

def test_basic_tracking():
    """Test basic API call tracking."""
    print("=" * 60)
    print("Testing Rate Limit Tracking")
    print("=" * 60)
    print("\n1. Starting rate_limit_checker.py in another terminal...")
    print("   Command: python rate_limit_checker.py\n")
    
    input("Press Enter when rate_limit_checker.py is running...")
    
    print("\n2. Making test API calls...\n")
    
    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    
    # Test 1: meta()
    print("   → Calling meta()...")
    start = time.time()
    try:
        result = info.meta()
        duration = time.time() - start
        log_api_call('meta', duration, 200)
        print(f"   ✓ Success ({duration*1000:.0f}ms) - logged to shared file")
    except Exception as e:
        duration = time.time() - start
        log_api_call('meta', duration, None, str(e))
        print(f"   ✗ Failed: {e}")
    
    time.sleep(1)
    
    # Test 2: all_mids()
    print("\n   → Calling all_mids()...")
    start = time.time()
    try:
        result = info.all_mids()
        duration = time.time() - start
        log_api_call('all_mids', duration, 200)
        print(f"   ✓ Success ({duration*1000:.0f}ms) - logged to shared file")
    except Exception as e:
        duration = time.time() - start
        log_api_call('all_mids', duration, None, str(e))
        print(f"   ✗ Failed: {e}")
    
    time.sleep(1)
    
    # Test 3: Multiple calls to simulate real usage
    print("\n3. Making 5 rapid calls to simulate real usage...")
    for i in range(5):
        print(f"   → Call {i+1}/5...")
        start = time.time()
        try:
            result = info.all_mids()
            duration = time.time() - start
            log_api_call('all_mids', duration, 200)
            print(f"   ✓ Success ({duration*1000:.0f}ms)")
        except Exception as e:
            duration = time.time() - start
            log_api_call('all_mids', duration, None, str(e))
            print(f"   ✗ Failed: {e}")
        time.sleep(0.5)
    
    print("\n" + "=" * 60)
    print("✓ Test Complete!")
    print("=" * 60)
    print("\nCheck the rate_limit_checker.py dashboard:")
    print("  • You should see ~7 API calls")
    print("  • Total Calls should increase")
    print("  • Recent API Calls should show 'meta' and 'all_mids'")
    print("  • Active Processes should show this script's PID")
    print("=" * 60)


if __name__ == "__main__":
    test_basic_tracking()

