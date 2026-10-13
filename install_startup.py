import os

startup_dir = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
bat_path = os.path.join(startup_dir, "Vighna_AutoStart.bat")

script_content = (
    "@echo off\r\n"
    "start \"\" /b \"C:\\Users\\krono\\AppData\\Local\\Programs\\Python\\Python311\\pythonw.exe\" "
    "\"C:\\Users\\krono\\Desktop\\Vighna\\startup_check.py\"\r\n"
)

with open(bat_path, "w", encoding="utf-8") as f:
    f.write(script_content)

print(f"Successfully installed auto-start launcher to: {bat_path}")
