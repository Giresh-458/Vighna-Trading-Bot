with open('app/position_manager.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if 'msg = (' in line and 'TRADE ENTERED' in lines[i+1]:
        new_lines.append('        msg = (\n')
        new_lines.append('            f"✅ **TRADE ENTERED**\\n"\n')
        new_lines.append('            f"#{sig[\'underlying\']} {opt_str}\\n"\n')
        new_lines.append('            f"Entry: {ltp}\\n"\n')
        new_lines.append('            f"Target: {sig[\'target_1\']}\\n"\n')
        new_lines.append('            f"SL: {sig[\'stop_loss\']}"\n')
        new_lines.append('        )\n')
        skip = True
    elif skip and line.strip() == ')':
        skip = False
    elif not skip:
        new_lines.append(line)

with open('app/position_manager.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
