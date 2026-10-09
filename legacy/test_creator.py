import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import fetcher
tokens = fetcher.get_new_tokens(5)
for t in tokens[:3]:
    c = t.get('creator','') or 'NOT FOUND'
    print(t['symbol'], '|', c[:24], '| MC:', t['usd_market_cap'])
