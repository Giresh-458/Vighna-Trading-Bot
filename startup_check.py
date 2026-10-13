import sys
import os
import subprocess
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST)

# 1. Check if weekday (0 = Monday, 4 = Friday)
if now_ist.weekday() > 4:
    sys.exit(0)

# 2. Check if between 08:55 and 23:00 IST
current_minutes = now_ist.hour * 60 + now_ist.minute
if not (8 * 60 + 55 <= current_minutes < 23 * 60):
    sys.exit(0)

# 3. Check if already running
try:
    import psutil
    for p in psutil.process_iter(['name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            if "app.main" in cmd:
                # Already active, do not spawn duplicate
                sys.exit(0)
        except Exception:
            pass
except Exception:
    pass

# 4. Launch start_bot.bat
base_dir = os.path.dirname(os.path.abspath(__file__))
bat_path = os.path.join(base_dir, "start_bot.bat")
if os.path.exists(bat_path):
    subprocess.Popen(["cmd.exe", "/c", bat_path], cwd=base_dir)
