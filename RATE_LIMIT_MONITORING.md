# Hyperliquid API Rate Limit Monitoring

## Overview

The rate limit monitoring system tracks API calls across all your scripts in real-time, helping you:
- ✅ Detect when you hit rate limits (HTTP 429 errors)
- 📊 Monitor calls per minute across all processes
- ⏱️  Track response times and detect slowdowns
- 🔍 See which endpoints you're calling most frequently
- 🚨 Get alerted before hitting rate limits

## Quick Start

### 1. Start the Rate Limit Monitor

In one terminal window:

```bash
cd /Users/vladimir.kovic/Code/lab/hyperliquid
source venv/bin/activate
python rate_limit_checker.py
```

This will show a live dashboard monitoring ALL API calls from ALL scripts.

### 2. Run Your Other Scripts

In another terminal window:

```bash
cd /Users/vladimir.kovic/Code/lab/hyperliquid
source venv/bin/activate
python hyperliquid_leverage_monitor.py
```

The rate limit checker will automatically detect and display API calls from the leverage monitor!

## What Gets Tracked

The `hyperliquid_leverage_monitor.py` has been updated to automatically track:
- ✅ All `user_state()` API calls
- ✅ Call duration
- ✅ Success/failure status
- ✅ Error messages if any

## Dashboard Sections

### Statistics Panel
- **Status**: Green (normal) or Red (rate limited)
- **Active Processes**: Number of scripts making API calls
- **Total API Calls**: All calls in the last 60 seconds
- **Calls/Minute**: Current rate (watch this!)
- **Response Times**: Min/Avg/Max latency

### Calls by Endpoint
Shows which API endpoints you're hitting most frequently:
- `user_state` - User position queries
- `meta` - Metadata queries
- `all_mids` - Price queries
- etc.

### Recent API Calls
Last 15 API calls with:
- Timestamp
- Endpoint name
- Response time (color-coded: green=fast, yellow=slow, red=very slow)
- Status code

### Rate Limit Events (if any)
Shows when you hit rate limits with timestamps and error messages.

## Understanding the Data

### Safe Call Rates
- **Recommended**: Keep under 10-20 calls/minute
- **Cache expiry**: The leverage monitor caches `user_state` for 60 seconds
- **Warning signs**: Response times > 1 second or HTTP 429 errors

### Common Patterns

**Good Pattern:**
```
Calls/Minute: 8.5
Avg Response: 150ms
Rate Limit Hits: 0
```

**Warning Pattern:**
```
Calls/Minute: 45.2  ⚠️ Too high!
Avg Response: 850ms  ⚠️ Slowing down
Rate Limit Hits: 0
```

**Rate Limited:**
```
Calls/Minute: 65.0  🚨
Avg Response: 1200ms  🚨
Rate Limit Hits: 3  🚨 RATE LIMITED!
```

## Additional Modes

### Stress Test Mode
Find your actual rate limit threshold:

```bash
python rate_limit_checker.py --test
```

⚠️ **Warning**: This will intentionally make rapid API calls to trigger rate limits.

### Analysis Mode
Get recommendations for your usage:

```bash
python rate_limit_checker.py --analyze
```

## Integration with Other Scripts

### Option 1: Automatic (Already Done for Leverage Monitor)

The leverage monitor is already integrated! Just run both scripts.

### Option 2: Manual Integration

Add to any other script:

```python
from rate_limit_checker import log_api_call
import time

# Before your API call
start = time.time()
try:
    result = info.user_state(address)
    log_api_call('user_state', time.time() - start, 200)
except Exception as e:
    log_api_call('user_state', time.time() - start, None, str(e))
```

### Option 3: Using TrackedInfo

Replace `Info` with `TrackedInfo` for automatic tracking:

```python
from rate_limit_checker import TrackedInfo
from hyperliquid.utils import constants

# Instead of: info = Info(constants.MAINNET_API_URL, skip_ws=True)
info = TrackedInfo(constants.MAINNET_API_URL, skip_ws=True)

# All calls are now automatically tracked!
info.user_state(address)  # Tracked
info.meta()               # Tracked
info.all_mids()          # Tracked
```

## Troubleshooting

### Monitor shows no API calls

1. Make sure your other script is running
2. Check that the log file exists: `ls -la /tmp/hyperliquid_api_calls.jsonl`
3. Make sure both scripts are using the same Python environment

### "Active Processes" shows 0

The process hasn't made any API calls in the last 60 seconds (outside the monitoring window).

### High calls/minute but no rate limit

You're approaching the limit but not there yet. Consider:
- Increasing cache expiry time
- Adding delays between calls
- Reducing monitored assets

## Log File Location

API calls are logged to: `/tmp/hyperliquid_api_calls.jsonl`

- Automatically cleaned up (keeps last 5 minutes by default)
- Thread-safe and multi-process safe
- Can be viewed directly: `tail -f /tmp/hyperliquid_api_calls.jsonl`

## Tips for Staying Under Rate Limits

1. **Increase Cache Time**: In `hyperliquid_leverage_monitor.py`, increase `CACHE_EXPIRY` from 60 to 120 seconds
2. **Monitor Fewer Assets**: Reduce the number of coins being monitored
3. **Add Delays**: Add `time.sleep(0.1)` between API calls
4. **Use WebSockets**: For real-time data, WebSockets don't count against rate limits
5. **Batch Requests**: If the API supports it, batch multiple queries into one call

## Example Session

```bash
# Terminal 1
$ python rate_limit_checker.py
Starting Cross-Process Rate Limit Monitor...
Monitoring API calls from all processes...

# Dashboard shows:
Status: ✓ Normal
Active Processes: 2
Total API Calls: 45
Calls/Minute: 8.2
Avg Response: 180ms

# Terminal 2
$ python hyperliquid_leverage_monitor.py
🎯 Monitoring Configuration:
   • Tracking trades with MARGIN ≥ $10,000 USD
   • Fetching leverage data for each trader
```

The rate limit checker will show every `user_state()` call made by the leverage monitor in real-time!

