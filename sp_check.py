# -*- coding: utf-8 -*-
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import scraper

for name, fn in scraper.SOURCES:
    t0 = time.time()
    try:
        rows = fn()
        print(f"[OK]   {name}: {len(rows)}건  ({time.time()-t0:.1f}s)")
        for r in rows[:2]:
            print("        ", r)
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}  ({time.time()-t0:.1f}s)")

print("--- 공컨/작업현황 ---")
for label, fn in [("BPTC work", scraper.fetch_bptc_work_status),
                  ("BPTC empty", scraper.fetch_bptc_empty_status),
                  ("GWCT empty", scraper.fetch_gwct_empty_status),
                  ("E1CT empty", scraper.fetch_e1ct_empty_status),
                  ("HJIT empty", scraper.fetch_hjit_empty_status)]:
    t0 = time.time()
    try:
        rows = fn()
        print(f"[OK]   {label}: {len(rows)}건 ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e} ({time.time()-t0:.1f}s)")
