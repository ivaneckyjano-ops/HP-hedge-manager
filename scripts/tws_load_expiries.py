#!/usr/bin/env python3
"""Load option expiries from TWS"""
import sys
sys.path.insert(0, '/home/narbon/Aplikácie/tws-webapp/venv/lib/python3.12/site-packages')
from ib_insync import IB, Option
import random

def main():
    if len(sys.argv) < 4:
        print("ERROR:Usage: tws_load_expiries.py PORT SYMBOL RIGHT")
        sys.exit(1)
    
    port = int(sys.argv[1])
    symbol = sys.argv[2]
    right = sys.argv[3]
    
    try:
        ib = IB()
        ib.connect('127.0.0.1', port, clientId=random.randint(1000,9999), readonly=True, timeout=20)
        
        opt = Option(symbol, '', 0, right, 'SMART')
        details = ib.reqContractDetails(opt)
        
        expiries = sorted(set(d.contract.lastTradeDateOrContractMonth for d in details))[:15]
        
        ib.disconnect()
        
        print(','.join(expiries))
            
    except Exception as e:
        print("ERROR:{}".format(str(e)), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
