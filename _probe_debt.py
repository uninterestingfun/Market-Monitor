import json
import time

import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}


def probe(name, url, **kw):
    t = time.time()
    try:
        r = requests.get(url, headers=H, timeout=20, **kw)
        body = r.text[:160].replace("\n", " ")
        print(f"{name:<18} {r.status_code} {len(r.text):>8} {time.time()-t:>5.1f}s {body!r}")
    except Exception as e:
        print(f"{name:<18} ERR {type(e).__name__} {str(e)[:60]}")


# 美国：财政部 debt to the penny
probe("US treasury", "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny",
      params={"sort": "-record_date", "page[size]": 1})

# 巴西：央行 SGS 13762（一般政府总债务占 GDP%）
probe("BR bcb", "https://api.bcb.gov.br/dados/serie/bcdata.sgs.13762/dados/ultimos/3",
      params={"formato": "json"})

# 英国：ONS 公共部门净债务
probe("UK ons", "https://api.beta.ons.gov.uk/v1/datasets")

# 加拿大：StatCan WDS（联邦债务占 GDP）
probe("CA statcan", "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods",
      json_data=None)

# 澳大利亚：ABS SDMX
probe("AU abs", "https://api.data.abs.gov.au/data/ABS,CTPI,1.10001.10.Q?lastNObservations=1",
      headers={**H, "Accept": "application/vnd.sdmx.data+json;version=1.0.0"})

# 日本：财务省
probe("JP mof", "https://www.mof.go.jp/english/policy/jgbs/reference/appendix/index.html")
