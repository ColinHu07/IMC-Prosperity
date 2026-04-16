import json, sys

def analyze(path):
    with open(path, 'r') as f:
        data = json.load(f)

    trades = data.get('tradeHistory', [])
    acts = data.get('activitiesLog', '')
    logs = data.get('logs', [])

    # Separate trades by symbol
    ash_buys = [t for t in trades if t['symbol']=='ASH_COATED_OSMIUM' and t.get('buyer')=='SUBMISSION']
    ash_sells = [t for t in trades if t['symbol']=='ASH_COATED_OSMIUM' and t.get('seller')=='SUBMISSION']
    pep_buys = [t for t in trades if t['symbol']=='INTARIAN_PEPPER_ROOT' and t.get('buyer')=='SUBMISSION']
    pep_sells = [t for t in trades if t['symbol']=='INTARIAN_PEPPER_ROOT' and t.get('seller')=='SUBMISSION']

    # ASH cash/pos
    ash_cash = 0
    ash_pos = 0
    for t in ash_buys:
        ash_cash -= t['price'] * t['quantity']
        ash_pos += t['quantity']
    for t in ash_sells:
        ash_cash += t['price'] * t['quantity']
        ash_pos -= t['quantity']

    # PEPPER cash/pos
    pep_cash = 0
    pep_pos = 0
    for t in pep_buys:
        pep_cash -= t['price'] * t['quantity']
        pep_pos += t['quantity']
    for t in pep_sells:
        pep_cash += t['price'] * t['quantity']
        pep_pos -= t['quantity']

    # Final mids from activitiesLog
    lines = [l for l in acts.split('\n') if l.strip()]
    last_ash_mid = last_pep_mid = None
    last_ash_pnl = last_pep_pnl = None
    for line in lines:
        parts = line.split(';')
        if len(parts) < 4:
            continue
        try:
            mid = float(parts[-2])
            pnl = float(parts[-1])
        except ValueError:
            continue
        if 'ASH' in parts[2]:
            last_ash_mid = mid
            last_ash_pnl = pnl
        elif 'PEPPER' in parts[2]:
            last_pep_mid = mid
            last_pep_pnl = pnl

    ash_total = ash_cash + ash_pos * (last_ash_mid or 0)
    pep_total = pep_cash + pep_pos * (last_pep_mid or 0)

    print(f'=== ASH_COATED_OSMIUM ===')
    print(f'Trades: {len(ash_buys)} buys ({sum(t["quantity"] for t in ash_buys)} units), {len(ash_sells)} sells ({sum(t["quantity"] for t in ash_sells)} units)')
    if ash_buys:
        print(f'Avg buy: {-ash_cash / ash_pos if ash_pos > 0 else sum(t["price"]*t["quantity"] for t in ash_buys)/sum(t["quantity"] for t in ash_buys):.1f}')
    if ash_sells:
        print(f'Avg sell: {sum(t["price"]*t["quantity"] for t in ash_sells)/sum(t["quantity"] for t in ash_sells):.1f}')
    print(f'Final pos: {ash_pos}, Final mid: {last_ash_mid}')
    print(f'Reported PnL: {last_ash_pnl}, Computed PnL: {ash_total:.0f}')

    print(f'\n=== INTARIAN_PEPPER_ROOT ===')
    print(f'Trades: {len(pep_buys)} buys ({sum(t["quantity"] for t in pep_buys)} units), {len(pep_sells)} sells ({sum(t["quantity"] for t in pep_sells)} units)')
    if pep_buys:
        print(f'Avg buy: {sum(t["price"]*t["quantity"] for t in pep_buys)/sum(t["quantity"] for t in pep_buys):.1f}')
    print(f'Final pos: {pep_pos}, Final mid: {last_pep_mid}')
    print(f'Reported PnL: {last_pep_pnl}, Computed PnL: {pep_total:.0f}')

    print(f'\n=== TOTAL: {ash_total + pep_total:.0f} (reported: {(last_ash_pnl or 0) + (last_pep_pnl or 0):.0f}) ===')

    # Position limit violations
    limit_errors = sum(1 for l in logs if 'exceeded limit' in l.get('sandboxLog', ''))
    print(f'\nPosition limit violations: {limit_errors}')

    # ASH fill rate
    ash_trade_ts = set()
    for t in trades:
        if t['symbol'] == 'ASH_COATED_OSMIUM' and 'SUBMISSION' in (t.get('buyer',''), t.get('seller','')):
            ash_trade_ts.add(t['timestamp'])
    print(f'ASH active timestamps: {len(ash_trade_ts)}/1000 ({len(ash_trade_ts)/10:.1f}%)')

    # ASH avg gap
    ash_times = sorted(ash_trade_ts)
    if len(ash_times) > 1:
        gaps = [ash_times[i+1] - ash_times[i] for i in range(len(ash_times)-1)]
        print(f'ASH avg gap: {sum(gaps)/len(gaps):.0f}, max gap: {max(gaps)}')

    # Pepper timeline
    print(f'\nPepper buy timeline:')
    cumpos = 0
    for t in sorted(pep_buys, key=lambda x: x['timestamp']):
        cumpos += t['quantity']
        print(f'  t={t["timestamp"]:>6} buy {t["quantity"]:>2} @ {t["price"]} -> pos={cumpos}')

    # PEPPER price drift
    pep_mids = []
    for line in lines:
        parts = line.split(';')
        if len(parts) > 3 and 'PEPPER' in parts[2]:
            try:
                pep_mids.append((int(parts[1]), float(parts[-2])))
            except:
                pass
    if pep_mids:
        print(f'\nPEPPER drift: {pep_mids[0][1]} -> {pep_mids[-1][1]} = +{pep_mids[-1][1]-pep_mids[0][1]:.1f}')

    # ASH spread stats
    ash_spreads = []
    for line in lines:
        parts = line.split(';')
        if len(parts) > 10 and 'ASH' in parts[2]:
            bid1 = parts[3]
            for i in range(3, len(parts)):
                if parts[i] == '' and i+1 < len(parts) and parts[i+1] == '':
                    ask_start = i+2
                    while ask_start < len(parts) and parts[ask_start] == '':
                        ask_start += 1
                    try:
                        ash_spreads.append(float(parts[ask_start]) - float(bid1))
                    except:
                        pass
                    break
    if ash_spreads:
        print(f'ASH spreads: avg={sum(ash_spreads)/len(ash_spreads):.1f}, n={len(ash_spreads)}')

if __name__ == '__main__':
    analyze(sys.argv[1])
