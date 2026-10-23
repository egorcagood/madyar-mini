"""Allowlisted home-screen data. Unknown is distinct from zero or task success."""
import math
import time


def _number(value,low,high):
    return type(value) in (int,float) and math.isfinite(value) and low<=value<=high


def normalise_limits(result,observed_at):
    if not isinstance(result,dict) or not _number(observed_at,1,1e12): return None
    buckets=result.get('rateLimitsByLimitId')
    bucket=buckets.get('codex') if isinstance(buckets,dict) else result.get('rateLimits')
    if not isinstance(bucket,dict) or bucket.get('limitId','codex')!='codex': return None
    windows={}
    for name in ('primary','secondary'):
        row=bucket.get(name)
        if not isinstance(row,dict): continue
        used,minutes,reset=(row.get(k) for k in ('usedPercent','windowDurationMins','resetsAt'))
        if (not _number(used,0,1000) or type(minutes) is not int or not 1<=minutes<=525600
                or type(reset) is not int or reset<=observed_at): continue
        windows[name]={'used_percent':used,'remaining_percent':max(0,min(100,100-used)),
                       'window_minutes':minutes,'resets_at':reset}
    if 'primary' not in windows: return None
    return {'observed_at':observed_at,'source':'codex_app_server','windows':windows}


def usage_view(snapshot,now=None,*,prefer_weekly=False):
    now=time.time() if now is None else now
    if not isinstance(snapshot,dict) or snapshot.get('source')!='codex_app_server': return None
    observed=snapshot.get('observed_at')
    if not _number(observed,1,1e12) or not -5<=now-observed<=600: return None
    windows=snapshot.get('windows',{})
    row=windows.get('primary')
    if prefer_weekly:
        for candidate in windows.values():
            if (isinstance(candidate,dict) and candidate.get('window_minutes')==10080
                    and _number(candidate.get('remaining_percent'),0,100)
                    and type(candidate.get('resets_at')) is int and candidate['resets_at']>now):
                row=candidate;break
    if not isinstance(row,dict) or not _number(row.get('remaining_percent'),0,100): return None
    if type(row.get('resets_at')) is not int or row['resets_at']<=now: return None
    minutes=row.get('window_minutes')
    if type(minutes) is not int or not 1<=minutes<=525600: return None
    label='неделя' if minutes==10080 else f'{minutes//60} ч' if minutes%60==0 else f'{minutes} мин'
    return dict(row,window_label=label,observed_at=observed)


def _weather(value,now):
    empty={'available':False,'description':'Нет данных','kind':'unknown',
           'temperature_c':None,'feels_c':None,'wind_ms':None}
    if not isinstance(value,dict) or value.get('source') not in ('local_feed','device_weather'): return empty
    when=value.get('observed_at')
    if not _number(when,1,1e12) or not -5<=now-when<=1800: return empty
    kind=value.get('kind')
    names={'snow':'Снег','rain':'Дождь','sun':'Солнечно','cloud':'Облачно','dew':'Роса'}
    if kind not in names or not _number(value.get('temperature_c'),-90,65): return empty
    return {'available':True,'kind':kind,'description':names[kind],
            'temperature_c':value['temperature_c'],
            'feels_c':value.get('feels_c') if _number(value.get('feels_c'),-100,80) else None,
            'wind_ms':value.get('wind_ms') if _number(value.get('wind_ms'),0,150) else None}


def home_snapshot(*,status_reply=None,usage=None,weather=None,now=None):
    now=time.time() if now is None else now
    codex={'status':'DISCONNECTED','working_count':0,'waiting_count':0,'label':None,'simulation':False}
    if isinstance(status_reply,dict) and status_reply.get('mode')=='simulation':
        state=status_reply.get('state')
        if isinstance(state,dict) and state.get('status') in ('CLOCK','WORKING','WARNING','DONE','UNKNOWN','DIAGNOSTIC'):
            counts=[state.get(k,0) for k in ('working_count','waiting_count')]
            if all(type(v) is int and 0<=v<=8 for v in counts):
                focus=state.get('focus')
                label=focus.get('label') if isinstance(focus,dict) else None
                if not isinstance(label,str) or len(label)>12: label=None
                codex.update(status=state['status'],working_count=counts[0],waiting_count=counts[1],
                             label=label,simulation=True)
    return {'schema':1,'server_time_ms':int(now*1000),'timezone':'UTC',
            'city':'Greenwich','country':'GB','device_ip':'',
            'weather':_weather(weather,now),'usage':usage_view(usage,now),'codex':codex,
            'physical_output_enabled':False}
