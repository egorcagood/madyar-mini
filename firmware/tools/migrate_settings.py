"""Explicit build-time migration of nonsecret settings; no network or device writes."""
import argparse
import json
import re
from pathlib import Path


def settings_header(stock):
    try:
        if stock['version'] != {'m':'SmallTV-Ultra','v':'Ultra-V9.0.43'}:
            raise ValueError('Unsupported factory snapshot')
        raw=stock['brightness']['brt']
        if not isinstance(raw,str) or not re.fullmatch(r'-?\d{1,3}',raw):
            raise ValueError('Unknown brightness')
        day=int(raw)
        night=stock['night'];en,start,end,level=(night[k] for k in ('en','t1','t2','b2'))
        if any(type(v) is not int for v in (en,start,end,level)) or en not in (0,1):
            raise ValueError('Invalid night settings')
        if not (-10<=day<=100 and -10<=level<=100 and 0<=start<=23 and 0<=end<=23):
            raise ValueError('Settings outside factory slider range')
    except (KeyError,TypeError) as exc:
        raise ValueError('Incomplete factory settings') from exc
    values=f'{{{day},{str(bool(en)).lower()},{start},{end},{level}}}'
    return '#pragma once\n#include "brightness.h"\nnamespace madyar {\nconstexpr BrightnessSettings displaySettings'+values+';\n}\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    source=json.loads(args.snapshot.read_text(encoding='utf-8'))
    args.output.write_text(settings_header(source.get('settings',source)),encoding='utf-8')
    print('Nonsecret brightness/night header generated; device not modified.')
