import json

with open('176962.log', 'r') as f:
    data = json.load(f)

acts = data.get('activitiesLog', '')
lines = [l for l in acts.split('\n') if l.strip()]

# Track PnL evolution
ash_pnls = []
pep_pnls = []
for line in lines:
    parts = line.split(';')
    if len(parts) > 3:
        try:
            ts = int(parts[1])
            pnl = float(parts[-1])
        except ValueError:
            continue
        if 'ASH' in parts[2]: ash_pnls.append((ts, pnl))
        if 'PEPPER' in parts[2]: pep_pnls.append((ts, pnl))

print('ASH PnL evolution (sampled):')
for ts, pnl in ash_pnls[::100]:
    print(f'  t={ts:>6}: {pnl:.1f}')
print(f'  t={ash_pnls[-1][0]:>6}: {ash_pnls[-1][1]:.1f}  (final)')

print()
print('PEPPER PnL evolution (sampled):')
for ts, pnl in pep_pnls[::100]:
    print(f'  t={ts:>6}: {pnl:.1f}')
print(f'  t={pep_pnls[-1][0]:>6}: {pep_pnls[-1][1]:.1f}  (final)')

# Trade frequency
trades = data.get('tradeHistory', [])
ash_trade_ts = set()
for t in trades:
    if t['symbol'] == 'ASH_COATED_OSMIUM' and 'SUBMISSION' in (t.get('buyer',''), t.get('seller','')):
        ash_trade_ts.add(t['timestamp'])
print(f'\nASH active timestamps: {len(ash_trade_ts)} out of 1000')
print(f'ASH fill rate: {len(ash_trade_ts)/10:.1f}%')

# Spread analysis  
ash_spreads = []
pep_spreads = []
for line in lines:
    parts = line.split(';')
    if len(parts) > 10:
        sym = parts[2]
        bid1 = parts[3]
        ask_start = None
        for i in range(3, len(parts)):
            if parts[i] == '' and i+1 < len(parts) and parts[i+1] == '':
                ask_start = i+2
                while ask_start < len(parts) and parts[ask_start] == '':
                    ask_start += 1
                break
        if ask_start and bid1 and ask_start < len(parts) - 2:
            try:
                best_bid = float(bid1)
                best_ask = float(parts[ask_start])
                spread = best_ask - best_bid
                if 'ASH' in sym:
                    ash_spreads.append(spread)
                elif 'PEPPER' in sym:
                    pep_spreads.append(spread)
            except:
                pass

if ash_spreads:
    print(f'\nASH spreads: avg={sum(ash_spreads)/len(ash_spreads):.1f}, min={min(ash_spreads)}, max={max(ash_spreads)}, n={len(ash_spreads)}')
if pep_spreads:
    print(f'PEP spreads: avg={sum(pep_spreads)/len(pep_spreads):.1f}, min={min(pep_spreads)}, max={max(pep_spreads)}, n={len(pep_spreads)}')

# Position limit violations
logs = data.get('logs', [])
limit_errors = sum(1 for l in logs if 'exceeded limit' in l.get('sandboxLog', ''))
print(f'\nPosition limit violations: {limit_errors}')

# Analyze ASH position over time
ash_position_events = []
for t in sorted(trades, key=lambda x: x['timestamp']):
    if t['symbol'] == 'ASH_COATED_OSMIUM':
        if t.get('buyer') == 'SUBMISSION':
            ash_position_events.append((t['timestamp'], t['quantity']))
        elif t.get('seller') == 'SUBMISSION':
            ash_position_events.append((t['timestamp'], -t['quantity']))

pos = 0
print('\nASH position trajectory:')
for ts, qty in ash_position_events:
    pos += qty
    if abs(pos) > 20 or ts % 10000 < 200:
        print(f'  t={ts:>6}: delta={qty:>+3} -> pos={pos:>+3}')
print(f'  Final pos: {pos}')

# Average time between ASH trades
ash_times = sorted(ash_trade_ts)
if len(ash_times) > 1:
    gaps = [ash_times[i+1] - ash_times[i] for i in range(len(ash_times)-1)]
    print(f'\nASH avg gap between trades: {sum(gaps)/len(gaps):.0f} ticks')
    print(f'ASH max gap: {max(gaps)} ticks')
