#!/usr/bin/env python3
"""Quick diagnostic: show station and packet age from packets.db."""
import sqlite3
import time
import sys
from datetime import datetime, timezone

DB_PATH = "/tmp/direwolf-dashboard/packets.db"
if len(sys.argv) > 1:
    DB_PATH = sys.argv[1]

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row
c = db.cursor()

now = time.time()
cutoff_2d = now - (2 * 86400)

print(f"=== DB Diagnostic: {DB_PATH} ===")
print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"2-day cutoff: {datetime.fromtimestamp(cutoff_2d).strftime('%Y-%m-%d %H:%M:%S')}")
print()

# Station summary
c.execute("SELECT COUNT(*) FROM stations")
total_stations = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM stations WHERE last_seen >= ?", (cutoff_2d,))
recent_stations = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM stations WHERE last_seen < ?", (cutoff_2d,))
stale_stations = c.fetchone()[0]

print(f"--- STATIONS ({total_stations} total) ---")
print(f"  Within 2 days: {recent_stations}")
print(f"  Older than 2 days: {stale_stations}")
print()

# Show all stations sorted by last_seen desc
c.execute("""
    SELECT callsign, last_seen, latitude, longitude, packet_count
    FROM stations ORDER BY last_seen DESC
""")
rows = c.fetchall()
if rows:
    print(f"  {'CALLSIGN':<12} {'LAST SEEN':<20} {'AGE':<14} {'LAT':>8} {'LON':>10} {'PKTS':>5}")
    print(f"  {'-'*12} {'-'*20} {'-'*14} {'-'*8} {'-'*10} {'-'*5}")
    for r in rows:
        age_sec = now - r["last_seen"]
        age_h = age_sec / 3600
        if age_h < 24:
            age_str = f"{age_h:.1f}h ago"
        else:
            age_str = f"{age_h/24:.1f}d ago"
        ts = datetime.fromtimestamp(r["last_seen"]).strftime("%Y-%m-%d %H:%M:%S")
        lat = f"{r['latitude']:.4f}" if r["latitude"] else "None"
        lon = f"{r['longitude']:.4f}" if r["longitude"] else "None"
        marker = " <-- STALE" if r["last_seen"] < cutoff_2d else ""
        print(f"  {r['callsign']:<12} {ts:<20} {age_str:<14} {lat:>8} {lon:>10} {r['packet_count']:>5}{marker}")
else:
    print("  (no stations)")

print()

# Packet summary
c.execute("SELECT COUNT(*) FROM packets")
total_packets = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM packets WHERE timestamp >= ?", (cutoff_2d,))
recent_packets = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM packets WHERE timestamp < ?", (cutoff_2d,))
stale_packets = c.fetchone()[0]

print(f"--- PACKETS ({total_packets} total) ---")
print(f"  Within 2 days: {recent_packets}")
print(f"  Older than 2 days: {stale_packets}")

# Show newest and oldest packet timestamps
c.execute("SELECT MIN(timestamp), MAX(timestamp) FROM packets")
row = c.fetchone()
if row[0]:
    oldest = datetime.fromtimestamp(row[0]).strftime("%Y-%m-%d %H:%M:%S")
    newest = datetime.fromtimestamp(row[1]).strftime("%Y-%m-%d %H:%M:%S")
    print(f"  Oldest packet: {oldest}")
    print(f"  Newest packet: {newest}")

db.close()
