#!/usr/bin/env python3
"""Fetch option premium from TWS"""
import sys
sys.path.insert(0, '/home/narbon/Aplikácie/tws-webapp/venv/lib/python3.12/site-packages')
from ib_insync import IB, Option
import random
import math

def main():
    if len(sys.argv) < 6:
        print("ERROR:Usage: tws_fetch_option.py PORT SYMBOL EXPIRY STRIKE RIGHT")
        sys.exit(1)
    
    port = int(sys.argv[1])
    symbol = sys.argv[2]
    expiry = sys.argv[3]
    strike = float(sys.argv[4])
    right = sys.argv[5]
    
    try:
        ib = IB()
        ib.connect('127.0.0.1', port, clientId=random.randint(1000,9999), readonly=True)
        
        opt = Option(symbol, expiry, strike, right, 'SMART')
        qualified = ib.qualifyContracts(opt)
        
        if not qualified:
            print("ERROR:Contract not found")
            sys.exit(1)
        
        # Try realtime first, fallback to delayed
        ib.reqMarketDataType(1)  # Realtime
        ticker = ib.reqMktData(opt, '106', False, False)  # genericTickList=106 (option greeks)
        ib.sleep(5)
        
        # If no data, try delayed
        if not ticker.bid and not ticker.ask and not ticker.last:
            ib.reqMarketDataType(3)
            ib.sleep(3)
        
        bid = ticker.bid if ticker.bid and not math.isnan(ticker.bid) and ticker.bid > 0 else 0
        ask = ticker.ask if ticker.ask and not math.isnan(ticker.ask) and ticker.ask > 0 else 0
        last = ticker.last if ticker.last and not math.isnan(ticker.last) and ticker.last > 0 else 0
        close = ticker.close if ticker.close and not math.isnan(ticker.close) and ticker.close > 0 else 0
        model = ticker.modelGreeks.optPrice if ticker.modelGreeks and ticker.modelGreeks.optPrice and not math.isnan(ticker.modelGreeks.optPrice) and ticker.modelGreeks.optPrice > 0 else 0
        
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        elif last > 0:
            mid = last
        elif close > 0:
            mid = close
        elif model > 0:
            mid = model
        else:
            mid = 0
        
        ib.cancelMktData(opt)
        ib.disconnect()
        
        if mid > 0:
            print("{:.2f}".format(mid))
        else:
            print("ERROR:No data (bid={}, ask={}, last={}, close={}, model={})".format(bid, ask, last, close, model))
            
    except Exception as e:
        print("ERROR:{}".format(str(e)))
        sys.exit(1)

if __name__ == '__main__':
    main()
