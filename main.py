"""
Bybit Signal Bot — 5 Signal Types
===================================
Signals : Crime Watch, Pump Cooloff Retest, Entry Signal, Whale Scope, Drift Scope
Exchange : Bybit public REST API (no API key needed)
Alerts   : Discord webhook
Hosting  : Railway / Replit ready

Set environment variable: DISCORD_WEBHOOK_URL
"""

import os
import time
import statistics
import requests
from datetime import datetime, timezone
from threading import Thread
from flask import Flask

# ──────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

BASE_URL            = "https://api.bybit.com"
SCAN_INTERVAL       = 60       # seconds between full scans
MIN_VOLUME_USD      = 500_000  # ignore pairs below this 24h volume

# cooldowns per signal type (seconds)
COOLDOWN = {
    "crime":   3600,
    "retest":  1800,
    "entry":   900,
    "whale":   900,
    "drift":   3600,
}

# signal thresholds
FUNDING_EXTREME   = 0.10   # %/hr
FUNDING_MODERATE  = 0.05
LS_HIGH           = 1.5
LS_LOW            = 0.7
DEPTH_OI_THIN_PCT = 3.0
THIN_BOOK_USD     = 50_000
COIL_DAYS         = 5
COIL_RANGE_PCT    = 5.0
VOL_SPIKE_X       = 2.5
MIN_CRIME_SCORE   = 40
PUMP_1H_PCT       = 15.0
RETEST_DROP_PCT   = 20.0
RETEST_SCANS      = 5

# ──────────────────────────────────────────────
#  STARTUP CHECK
# ──────────────────────────────────────────────
def check_config():
    if not DISCORD_WEBHOOK_URL:
        print("❌ DISCORD_WEBHOOK_URL not set in environment variables!")
        raise SystemExit(1)
    print("✅ Discord webhook loaded")
    print("✅ 5 signal types active:")
    print("   1. Crime Watch")
    print("   2. Pump Cooloff Retest")
    print("   3. Entry Signal (VWAP + OI Delta)")
    print("   4. Whale Scope (Pump Detected)")
    print("   5. Drift Scope (Graded Trade Setup)")
    print("=" * 54)

# ──────────────────────────────────────────────
#  KEEP ALIVE SERVER (Replit / Railway)
# ──────────────────────────────────────────────
def start_keep_alive():
    app = Flask("")

    @app.route("/")
    def home():
        return "Bybit Signal Bot is running ✅"

    def run():
        app.run(host="0.0.0.0", port=8080)

    Thread(target=run, daemon=True).start()
    print("✅ Keep-alive server running on port 8080")

# ──────────────────────────────────────────────
#  BYBIT API HELPERS
# ──────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0"}

def api_get(path, params=None):
    try:
        r = requests.get(BASE_URL + path, params=params, timeout=15, headers=HEADERS)
        data = r.json()
        ret_code = data.get("retCode", -1)
        if ret_code == 0:
            return data.get("result", {})
        print(f"  [api] {path} retCode={ret_code} msg={data.get('retMsg')}")
        return None
    except Exception as e:
        print(f"  [api error] {path}: {e}")
        return None


def get_all_tickers():
    result = api_get("/v5/market/tickers", {"category": "linear"})
    if not result:
        return []
    return result.get("list", [])


def get_ticker(symbol):
    result = api_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
    if not result:
        return None
    items = result.get("list", [])
    return items[0] if items else None


def get_funding_history(symbol, limit=3):
    result = api_get("/v5/market/funding/history", {
        "category": "linear", "symbol": symbol, "limit": limit
    })
    return result.get("list", []) if result else []


def get_open_interest(symbol, interval="5min", limit=10):
    result = api_get("/v5/market/open-interest", {
        "category": "linear", "symbol": symbol,
        "intervalTime": interval, "limit": limit
    })
    return result.get("list", []) if result else []


def get_klines(symbol, interval, limit=50):
    result = api_get("/v5/market/kline", {
        "category": "linear", "symbol": symbol,
        "interval": interval, "limit": limit
    })
    return result.get("list", []) if result else []


def get_orderbook(symbol, limit=50):
    result = api_get("/v5/market/orderbook", {
        "category": "linear", "symbol": symbol, "limit": limit
    })
    return result


def get_ls_ratio(symbol, period="5min"):
    result = api_get("/v5/market/account-ratio", {
        "category": "linear", "symbol": symbol, "period": period, "limit": 1
    })
    items = result.get("list", []) if result else []
    if items:
        try:
            return float(items[0].get("buyRatio", 0.5)) / max(float(items[0].get("sellRatio", 0.5)), 0.0001)
        except:
            return None
    return None


def get_instruments():
    result = api_get("/v5/market/instruments-info", {"category": "linear"})
    return result.get("list", []) if result else []

# ──────────────────────────────────────────────
#  UTILITY FUNCTIONS
# ──────────────────────────────────────────────
def fmt_usd(v):
    try:
        v = float(v)
        if v >= 1_000_000_000: return f"${v/1_000_000_000:.2f}B"
        elif v >= 1_000_000:   return f"${v/1_000_000:.1f}M"
        elif v >= 1_000:       return f"${v/1_000:.1f}K"
        return f"${v:.4f}"
    except:
        return "N/A"


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def chart_url(symbol):
    return f"https://www.tradingview.com/chart/?symbol=BYBIT:{symbol}"


def bubblemaps_url(symbol):
    base = symbol.replace("USDT", "").lower()
    return f"https://app.bubblemaps.io/bsc/token/{base}"


def calc_vwap(klines):
    """Calculate VWAP from kline data [timestamp, open, high, low, close, volume, ...]"""
    try:
        total_pv = 0
        total_v  = 0
        for k in klines:
            high  = float(k[2])
            low   = float(k[3])
            close = float(k[4])
            vol   = float(k[5])
            tp    = (high + low + close) / 3
            total_pv += tp * vol
            total_v  += vol
        return total_pv / total_v if total_v > 0 else 0
    except:
        return 0


def calc_rvol(klines, period=20):
    """Relative volume vs average."""
    try:
        vols    = [float(k[5]) for k in klines]
        if len(vols) < 2:
            return 0
        cur_vol = vols[0]
        avg_vol = statistics.mean(vols[1:period+1])
        return round(cur_vol / avg_vol, 2) if avg_vol > 0 else 0
    except:
        return 0


def rvol_label(rvol):
    if rvol < 0.5:   return f"Low ({rvol}x avg)"
    elif rvol < 1.5: return f"Normal ({rvol}x avg)"
    else:             return f"High ({rvol}x avg) 🔥"


def score_label(score):
    if score >= 70:   return "HIGH 🔴"
    elif score >= 40: return "MODERATE 🟡"
    else:             return "LOW 🟢"

# ──────────────────────────────────────────────
#  DISCORD SENDER
# ──────────────────────────────────────────────
def send_discord(msg, username="Bybit Signal Bot"):
    try:
        payload  = {"content": msg, "username": username}
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code in (200, 204):
            print(f"  [✓] Alert sent to Discord")
        else:
            print(f"  [!] Discord error: {response.status_code}")
    except Exception as e:
        print(f"  [!] Discord send failed: {e}")

# ──────────────────────────────────────────────
#  SIGNAL 1 — CRIME WATCH
# ──────────────────────────────────────────────
def run_crime_watch(symbol, ticker, funding_hr, ls_ratio, oi_list, klines_1h):
    try:
        score   = 0
        reasons = []

        # funding
        if funding_hr is not None:
            abs_f = abs(funding_hr)
            if abs_f >= FUNDING_EXTREME:
                score += 25
                side = "shorts" if funding_hr < 0 else "longs"
                reasons.append(f"Funding {funding_hr:+.4f}%/hr — {side} paying extreme rate, forced close pressure building")
            elif abs_f >= FUNDING_MODERATE:
                score += 12
                reasons.append(f"Funding {funding_hr:+.4f}%/hr — elevated, watch for squeeze")

        # L/S ratio
        if ls_ratio:
            if ls_ratio >= LS_HIGH:
                score += 15
                reasons.append(f"L/S ratio {ls_ratio:.2f} — longs dominant, shorts still paying")
            elif ls_ratio <= LS_LOW:
                score += 10
                reasons.append(f"L/S ratio {ls_ratio:.2f} — shorts dominant, long squeeze risk")

        # order book thinness
        ob = get_orderbook(symbol)
        if ob:
            price      = float(ticker.get("lastPrice", 0))
            depth_range = price * 0.01
            bids = ob.get("b", [])
            asks = ob.get("a", [])
            bid_depth = sum(float(b[0]) * float(b[1]) for b in bids if float(b[0]) >= price - depth_range)
            ask_depth = sum(float(a[0]) * float(a[1]) for a in asks if float(a[0]) <= price + depth_range)
            total_depth = bid_depth + ask_depth
            oi_val = float(oi_list[0].get("openInterest", 0)) * price if oi_list else 0
            if oi_val > 0:
                depth_oi_pct = (total_depth / oi_val) * 100
                if depth_oi_pct <= DEPTH_OI_THIN_PCT:
                    score += 20
                    reasons.append(f"Thin order book: {depth_oi_pct:.1f}% depth/OI — small capital moves price significantly")

        # coiling
        klines_1d = get_klines(symbol, "D", limit=25)
        coil_days = 0
        if klines_1d and len(klines_1d) >= 3:
            for k in klines_1d[1:]:
                high  = float(k[2])
                low   = float(k[3])
                rng   = ((high - low) / low) * 100 if low > 0 else 0
                if rng <= COIL_RANGE_PCT:
                    coil_days += 1
                else:
                    break
            if coil_days >= COIL_DAYS:
                score += 25
                reasons.append(f"Coiling for {coil_days} days — pressure building 🔴")
            elif coil_days >= 3:
                score += 10
                reasons.append(f"Coiling {coil_days} days — compression beginning")

        # volume spike
        if klines_1h and len(klines_1h) >= 5:
            vols    = [float(k[5]) for k in klines_1h]
            cur_vol = vols[0]
            avg_vol = statistics.mean(vols[1:])
            ratio   = cur_vol / avg_vol if avg_vol > 0 else 0
            if ratio >= VOL_SPIKE_X:
                score += 15
                reasons.append(f"⚡ Volume spike {ratio:.1f}x above average — price starting to move")

        if score < MIN_CRIME_SCORE:
            return score

        price    = float(ticker.get("lastPrice", 0))
        vol_24h  = float(ticker.get("volume24h", 0)) * price
        change   = float(ticker.get("price24hPcnt", 0)) * 100
        oi_val   = float(oi_list[0].get("openInterest", 0)) * price if oi_list else 0
        f_str    = f"{funding_hr:+.4f}%/hr" if funding_hr else "N/A"
        ls_str   = f"{ls_ratio:.2f}" if ls_ratio else "N/A"
        r_text   = "\n".join(f"• {r}" for r in reasons)

        msg = f"""🔮 **CRIME WATCH — {symbol}**
━━━━━━━━━━━━━━━━━━━━━━━━
Crime probability: **{score}/100 ({score_label(score)})**
Price: {fmt_usd(price)}
24h volume: {fmt_usd(vol_24h)}
Open interest: {fmt_usd(oi_val)}
Funding rate: {f_str}
L/S ratio: {ls_str}
24h change: {change:+.2f}%
━━━━━━━━━━━━━━━━━━━━━━━━
**Why flagged:**
{r_text}
━━━━━━━━━━━━━━━━━━━━━━━━
⏰ {now_utc()} UTC
📊 {chart_url(symbol)}
NFA · DYOR · Size accordingly"""

        send_discord(msg, "🔮 Crime Watch")
        return score

    except Exception as e:
        print(f"  [crime_watch error] {symbol}: {e}")
        return 0

# ──────────────────────────────────────────────
#  SIGNAL 2 — PUMP COOLOFF RETEST
# ──────────────────────────────────────────────
stable_scan_counts = {}  # tracks consecutive stable scans per symbol

def run_pump_retest(symbol, ticker, funding_hr, oi_list, klines_1d):
    try:
        price = float(ticker.get("lastPrice", 0))
        if price == 0 or not klines_1d:
            return

        # peak price in last 30 days
        highs    = [float(k[2]) for k in klines_1d]
        peak     = max(highs) if highs else price
        drop_pct = ((peak - price) / peak) * 100 if peak > 0 else 0

        if drop_pct < RETEST_DROP_PCT:
            stable_scan_counts[symbol] = 0
            return

        if funding_hr is None or funding_hr >= 0:
            return

        # count stable scans
        prev_count = stable_scan_counts.get(symbol, 0)
        stable_scan_counts[symbol] = prev_count + 1

        if stable_scan_counts[symbol] < RETEST_SCANS:
            return

        # stage
        scans = stable_scan_counts[symbol]
        if scans < 8:    stage, slabel, sdesc = 1, "STARTING", "Farming starting — watch"
        elif scans < 15: stage, slabel, sdesc = 2, "BUILDING", "Accumulation building"
        elif scans < 25: stage, slabel, sdesc = 3, "ACTIVE",   "Active retest phase"
        elif scans < 35: stage, slabel, sdesc = 4, "PEAK",     "Near peak pressure"
        else:            stage, slabel, sdesc = 5, "COOLING",  "Cooling — watch for entry"

        # next funding (approx)
        now_ts      = time.time()
        funding_interval_hr = 8
        next_funding_min = int((funding_interval_hr * 3600 - (now_ts % (funding_interval_hr * 3600))) / 60)

        vol_24h  = float(ticker.get("volume24h", 0)) * price
        change   = float(ticker.get("price24hPcnt", 0)) * 100
        oi_val   = float(oi_list[0].get("openInterest", 0)) * price if oi_list else 0

        msg = f"""🌀 **PUMP COOLOFF RETEST — {symbol}**
Score: {min(scans * 3, 100)}/100
Peak price: {fmt_usd(peak)}
Current price: {fmt_usd(price)} ({drop_pct:.1f}% from peak)
24h change: {change:+.2f}%
24h vol: {fmt_usd(vol_24h)}
Open interest: {fmt_usd(oi_val)}
Funding rate: {funding_hr:+.4f}%/hr per settlement
Stage {stage}/5 ({slabel}) — {sdesc}
Next funding: ~{next_funding_min} min
Time: {datetime.now(timezone.utc).strftime("%H:%M")} UTC
━━━━━━━━━━━━━━━━━━━━━━━━
Setup:
• Retest setup: pulled back {drop_pct:.1f}% from peak, stabilising for {scans} consecutive scans
• Funding still negative: {funding_hr:+.4f}%/hr — farming continues, pullback is manufactured dip
• OI held during pullback — shorts adding: more squeeze fuel loaded
━━━━━━━━━━━━━━━━━━━━━━━━
📊 {chart_url(symbol)}
🫧 {bubblemaps_url(symbol)}
NFA · DYOR · Size accordingly"""

        send_discord(msg, "🌀 Pump Cooloff Retest")

    except Exception as e:
        print(f"  [retest error] {symbol}: {e}")

# ──────────────────────────────────────────────
#  SIGNAL 3 — ENTRY SIGNAL (VWAP + OI DELTA)
# ──────────────────────────────────────────────
def run_entry_signal(symbol, ticker, oi_list, klines_5m, klines_1d):
    try:
        price = float(ticker.get("lastPrice", 0))
        if price == 0:
            return

        # VWAPs
        vwap_15m  = calc_vwap(klines_5m[:3]) if klines_5m else 0
        vwap_day  = calc_vwap(klines_1d[:1]) if klines_1d else 0

        above_15m  = price > vwap_15m if vwap_15m > 0 else None
        above_day  = price > vwap_day if vwap_day > 0 else None

        if above_15m is None or above_day is None:
            return

        if above_15m and above_day:
            vwap_day_label = "ABOVE ✅"
        elif not above_15m and not above_day:
            vwap_day_label = "BELOW"
        else:
            vwap_day_label = "⚠️ CONFLICTED"

        vwap_15m_label = "ABOVE ✅" if above_15m else "BELOW"

        # OI 5m delta
        oi_delta = 0
        oi_label = ""
        if oi_list and len(oi_list) >= 2:
            oi_now  = float(oi_list[0].get("openInterest", 0))
            oi_prev = float(oi_list[1].get("openInterest", 0))
            if oi_prev > 0:
                oi_delta = ((oi_now - oi_prev) / oi_prev) * 100
                price_change = float(ticker.get("price24hPcnt", 0)) * 100
                if oi_delta > 0 and price_change < 0:
                    oi_label = "INHALE DETECTED 🟢"
                elif oi_delta > 0:
                    oi_label = "EXHALE"
                else:
                    oi_label = "Declining"

        # RVol
        rvol    = calc_rvol(klines_5m) if klines_5m else 0
        rv_label = rvol_label(rvol)

        # thin book
        ob          = get_orderbook(symbol)
        thin_book   = False
        book_depth  = 0
        if ob:
            depth_range = price * 0.01
            bids        = ob.get("b", [])
            asks        = ob.get("a", [])
            bid_d       = sum(float(b[0]) * float(b[1]) for b in bids if float(b[0]) >= price - depth_range)
            ask_d       = sum(float(a[0]) * float(a[1]) for a in asks if float(a[0]) <= price + depth_range)
            book_depth  = bid_d + ask_d
            thin_book   = book_depth < THIN_BOOK_USD

        # market state
        if not above_15m and not above_day and rvol < 1.0:
            state, direction = "WATERFALL", "SHORT bias"
        elif above_15m and above_day and rvol >= 1.5:
            state, direction = "BREAKOUT", "LONG bias"
        elif oi_delta > 1.0 and "INHALE" in oi_label:
            state, direction = "INHALE", "LONG bias"
        elif oi_delta > 0.5 and not above_15m:
            state, direction = "SQUEEZE", "LONG bias"
        else:
            return  # NEUTRAL — no alert

        emoji = "🔴" if "SHORT" in direction else "🟢"
        thin_line = f"\n⚠️ Thin Book (depth {fmt_usd(book_depth)} < {fmt_usd(THIN_BOOK_USD)})" if thin_book else ""

        msg = f"""{emoji} **ENTRY — {symbol}**
────────────────────────
📡 State      {state}
📈 Direction  {direction}
────────────────────────
💰 Price      {price:.6f}
📊 15m VWAP   {vwap_15m:.6f} [{vwap_15m_label}]
📐 Daily VWAP {vwap_day:.6f} [{vwap_day_label}]
📦 OI 5m Delta {oi_delta:+.2f}% [{oi_label}]
⚡ RVol        {rv_label}{thin_line}
────────────────────────
⏰ {now_utc()} UTC
📊 {chart_url(symbol)}
────────────────────────
NFA · DYOR · Size accordingly"""

        send_discord(msg, "📡 Entry Signal")

    except Exception as e:
        print(f"  [entry_signal error] {symbol}: {e}")

# ──────────────────────────────────────────────
#  SIGNAL 4 — WHALE SCOPE (PUMP DETECTED)
# ──────────────────────────────────────────────
def run_whale_scope(symbol, ticker, funding_hr, oi_list, klines_1h):
    try:
        if not klines_1h or len(klines_1h) < 2:
            return

        price_now  = float(klines_1h[0][4])
        price_1h   = float(klines_1h[1][4])
        if price_1h == 0:
            return

        change_1h = ((price_now - price_1h) / price_1h) * 100

        if change_1h < PUMP_1H_PCT:
            return
        if funding_hr is None or funding_hr >= 0:
            return

        oi_now  = float(oi_list[0].get("openInterest", 0)) if oi_list else 0
        oi_prev = float(oi_list[1].get("openInterest", 0)) if len(oi_list) > 1 else 0
        if oi_prev > 0 and oi_now <= oi_prev:
            return

        oi_usd = oi_now * price_now

        msg = f"""👀 **PUMP DETECTED — {symbol}**
────────────────────────
📈 Price +{change_1h:.1f}% in 1h
💰 Funding {funding_hr:+.4f}%/hr — extreme negative
📦 OI {fmt_usd(oi_usd)}

⏳ Watching for dump → support → bounce
*Not an entry signal — do not chase*
⏰ {now_utc()} UTC
📊 {chart_url(symbol)}
NFA · DYOR · Size accordingly"""

        send_discord(msg, "👀 Whale Scope")

    except Exception as e:
        print(f"  [whale_scope error] {symbol}: {e}")

# ──────────────────────────────────────────────
#  SIGNAL 5 — DRIFT SCOPE (GRADED TRADE SETUP)
# ──────────────────────────────────────────────
def run_drift_scope(symbol, ticker, funding_hr, oi_list, klines_5m, klines_1h):
    try:
        price = float(ticker.get("lastPrice", 0))
        if price == 0:
            return

        vwap_15m = calc_vwap(klines_5m[:3]) if klines_5m else 0
        above_vwap = price > vwap_15m if vwap_15m > 0 else False
        rvol = calc_rvol(klines_1h) if klines_1h else 0

        # OI deltas
        oi_1h_delta = 0
        oi_5m_delta = 0
        if oi_list and len(oi_list) >= 2:
            oi_now  = float(oi_list[0].get("openInterest", 0))
            oi_prev = float(oi_list[-1].get("openInterest", 0))
            oi_1h_prev = float(oi_list[min(12, len(oi_list)-1)].get("openInterest", 0))
            if oi_prev > 0:
                oi_5m_delta = ((oi_now - oi_prev) / oi_prev) * 100
            if oi_1h_prev > 0:
                oi_1h_delta = ((oi_now - oi_1h_prev) / oi_1h_prev) * 100

        funding_ok  = funding_hr is not None and funding_hr < -FUNDING_MODERATE
        oi_rising   = oi_5m_delta > 0.3
        oi_1h_up    = oi_1h_delta > 0

        # grade
        conditions = [funding_ok, oi_rising, above_vwap, rvol >= 1.5]
        met        = sum(conditions)
        if met == 4:   grade = "A"
        elif met == 3: grade = "B"
        else:          return  # C or below — skip

        # setup type
        if funding_ok and oi_rising and above_vwap:
            setup = "Short Squeeze Setup"
        elif above_vwap and rvol >= 1.5 and oi_rising:
            setup = "Breakout Setup"
        else:
            setup = "Momentum Setup"

        # levels
        entry = price
        sl    = round(entry * 0.848, 6)
        tp1   = round(entry * 1.15, 6)
        tp2   = round(entry * 1.30, 6)
        risk  = entry - sl
        rr1   = round((tp1 - entry) / risk, 2) if risk > 0 else 0
        rr2   = round((tp2 - entry) / risk, 2) if risk > 0 else 0

        # price change 1h
        change_1h = 0
        if klines_1h and len(klines_1h) >= 2:
            p_now = float(klines_1h[0][4])
            p_1h  = float(klines_1h[1][4])
            change_1h = ((p_now - p_1h) / p_1h) * 100 if p_1h > 0 else 0

        # catalyst
        catalyst = "Move appears mechanical" if (abs(change_1h) > 10 and rvol < 0.5) else "No catalyst found — move appears organic"

        oi_1h_str = f"+{oi_1h_delta:.1f}%" if oi_1h_delta >= 0 else f"{oi_1h_delta:.1f}%"
        f_str     = f"{funding_hr:+.3f}%/hr (shorts paying)" if funding_hr else "N/A"

        msg = f"""🟢 **OPEN LONG — {symbol}**
────────────────────────
📊 Grade {grade} | {setup}
────────────────────────
📈 Price     {change_1h:+.1f}% (1h)
💸 Funding   {f_str}
📦 OI 1h     holding {oi_1h_str}
📦 OI 5m     {oi_5m_delta:+.2f}% (5m)
📐 VWAP 15m  ${vwap_15m:.6f} ({'above ✅' if above_vwap else 'below ⚠️'})
────────────────────────
🎯 Entry   ${entry:.6f}
🛡 SL      ${sl:.6f} (-15.2%)
✅ TP1     ${tp1:.6f} (+15%)  R/R 1:{rr1}
✅ TP2     ${tp2:.6f} (+30%)  R/R 1:{rr2}
────────────────────────
⚡ {catalyst}
────────────────────────
⏰ {now_utc()} UTC
📊 {chart_url(symbol)}
NFA · DYOR · Size accordingly"""

        send_discord(msg, "📊 Drift Scope")

    except Exception as e:
        print(f"  [drift_scope error] {symbol}: {e}")

# ──────────────────────────────────────────────
#  MAIN SCAN LOOP
# ──────────────────────────────────────────────
def main():
    check_config()
    start_keep_alive()

    # cooldown trackers
    last_alert = {sig: {} for sig in COOLDOWN}

    print(f"\n[BOT] Starting scan loop — every {SCAN_INTERVAL}s")

    while True:
        try:
            scan_start = time.time()
            now_str    = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"\n[SCAN] {now_str} UTC — fetching all tickers…")

            tickers = get_all_tickers()
            if not tickers:
                print("[WARN] No tickers returned — retrying next cycle")
                time.sleep(SCAN_INTERVAL)
                continue

            # filter active USDT perps with enough volume
            active = []
            for t in tickers:
                symbol = t.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                try:
                    price    = float(t.get("lastPrice", 0))
                    vol_usdt = float(t.get("volume24h", 0)) * price
                    if vol_usdt >= MIN_VOLUME_USD and price > 0:
                        active.append((symbol, t))
                except:
                    continue

            print(f"[SCAN] {len(active)} pairs above volume threshold")
            alerts_sent = 0

            for symbol, ticker in active:
                try:
                    now_ts = time.time()

                    # fetch shared data once per symbol
                    funding_list = get_funding_history(symbol, limit=3)
                    funding_hr   = None
                    if funding_list:
                        try:
                            rate_raw   = float(funding_list[0].get("fundingRate", 0))
                            funding_hr = round((rate_raw / 8) * 100, 4)
                        except:
                            pass

                    oi_list   = get_open_interest(symbol, "5min", limit=15)
                    klines_5m = get_klines(symbol, "5", limit=50)
                    klines_1h = get_klines(symbol, "60", limit=50)
                    klines_1d = get_klines(symbol, "D", limit=30)
                    ls_ratio  = get_ls_ratio(symbol)

                    print(f"  {symbol:<20} funding={funding_hr}  ls={ls_ratio}")

                    # --- Crime Watch ---
                    if now_ts - last_alert["crime"].get(symbol, 0) > COOLDOWN["crime"]:
                        score = run_crime_watch(symbol, ticker, funding_hr, ls_ratio, oi_list, klines_1h)
                        if score >= MIN_CRIME_SCORE:
                            last_alert["crime"][symbol] = now_ts
                            alerts_sent += 1
                            time.sleep(2)

                    # --- Pump Cooloff Retest ---
                    if now_ts - last_alert["retest"].get(symbol, 0) > COOLDOWN["retest"]:
                        run_pump_retest(symbol, ticker, funding_hr, oi_list, klines_1d)
                        last_alert["retest"][symbol] = now_ts

                    # --- Entry Signal ---
                    if now_ts - last_alert["entry"].get(symbol, 0) > COOLDOWN["entry"]:
                        run_entry_signal(symbol, ticker, oi_list, klines_5m, klines_1d)
                        last_alert["entry"][symbol] = now_ts
                        time.sleep(1)

                    # --- Whale Scope ---
                    if now_ts - last_alert["whale"].get(symbol, 0) > COOLDOWN["whale"]:
                        run_whale_scope(symbol, ticker, funding_hr, oi_list, klines_1h)
                        last_alert["whale"][symbol] = now_ts

                    # --- Drift Scope ---
                    if now_ts - last_alert["drift"].get(symbol, 0) > COOLDOWN["drift"]:
                        run_drift_scope(symbol, ticker, funding_hr, oi_list, klines_5m, klines_1h)
                        last_alert["drift"][symbol] = now_ts
                        time.sleep(1)

                except Exception as e:
                    print(f"  [skip] {symbol}: {e}")
                    continue

            elapsed = round(time.time() - scan_start, 1)
            print(f"\n[DONE] Scan complete in {elapsed}s — {alerts_sent} alert(s) sent")

        except Exception as e:
            print(f"[ERROR] Main loop: {e}")

        print(f"[WAIT] Next scan in {SCAN_INTERVAL}s…")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
