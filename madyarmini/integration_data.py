"""Bounded PC data; notification contents are deliberately RAM-only."""
import copy
import math
import re
import time
import unicodedata
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from collections import OrderedDict, deque
from .device import validate_host


def clean_text(value,chars,byte_limit):
    if not isinstance(value,str):raise ValueError('text_type')
    value=unicodedata.normalize('NFC',value.replace('\r',' ').replace('\n',' '))
    value=''.join(c for c in value if not unicodedata.category(c).startswith('C'))[:chars]
    while len(value.encode('utf-8'))>byte_limit:value=value[:-1]
    return value.strip()


def default_settings():
    return dict(device_host='',brightness=19,night_enabled=False,night_start=22,
                night_end=7,night_brightness=100,done_seconds=10,notice_seconds=8,sound=False,
                paused=False,telegram_enabled=True,auto_track=True,show_ip=True,ip_text='',background_solid=False,
                animations_enabled=True,animation_seconds=3,animation_interval_seconds=60,clock_style='classic',
                palette=dict(hour_from='#edfbff',hour_to='#168dbf',background_from='#020911',background_to='#07304a',frame='#105070'),
                place={'name':'Greenwich','country':'GB','latitude':51.48,
                       'longitude':0.0,'timezone':'UTC'})


def validate_settings(value):
    defaults=default_settings()
    if isinstance(value,dict):
        value=dict(value)
        # Add only new presentation fields; never reset old user choices.
        for key in ('palette','show_ip','animations_enabled','animation_seconds','animation_interval_seconds','clock_style','ip_text','background_solid'):
            if key not in value:value[key]=copy.deepcopy(defaults[key])
    if not isinstance(value,dict) or set(value)!=set(defaults):raise ValueError('settings_fields')
    out=copy.deepcopy(value)
    if not isinstance(out['clock_style'],str) or out['clock_style'] not in ('classic','rounded','type'):
        raise ValueError('clock_style')
    try:
        if out['device_host'] != '':out['device_host']=validate_host(out['device_host'])
    except (RuntimeError,TypeError):raise ValueError('device_host') from None
    if (not isinstance(out['ip_text'],str) or not re.fullmatch(r'[A-Za-zА-Яа-яЁё0-9 ._:-]{0,15}',out['ip_text'])
            or out['ip_text'].strip()!=out['ip_text']):raise ValueError('ip_text')
    for key in ('sound','paused','telegram_enabled','auto_track','night_enabled','show_ip','animations_enabled','background_solid'):
        if type(out[key]) is not bool:raise ValueError('settings_bool')
    palette=out['palette']
    if isinstance(palette,dict) and 'frame' not in palette:palette['frame']=defaults['palette']['frame']
    if not isinstance(palette,dict) or set(palette)!=set(defaults['palette']):raise ValueError('palette_fields')
    for key,color in palette.items():
        if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):raise ValueError('palette_color')
        palette[key]=color.lower()
    for key,low,high in [('brightness',0,100),('night_brightness',0,100),('night_start',0,23),
                         ('night_end',0,23),('done_seconds',1,60),('notice_seconds',3,15),
                         ('animation_seconds',1,10),('animation_interval_seconds',15,3600)]:
        if type(out[key]) is not int or not low<=out[key]<=high:raise ValueError('settings_range')
    p=out['place']
    if not isinstance(p,dict) or set(p)!={'name','country','latitude','longitude','timezone'}:
        raise ValueError('place_fields')
    if (not isinstance(p['name'],str) or not 1<=len(p['name'])<=40 or
        clean_text(p['name'],40,120)!=p['name'] or any(c in p['name'] for c in '<>')):
        raise ValueError('place_name')
    if not isinstance(p['country'],str) or not re.fullmatch('[A-Z]{2}',p['country']):raise ValueError('place_country')
    for k,lo,hi in [('latitude',-90,90),('longitude',-180,180)]:
        if type(p[k]) not in (int,float) or not math.isfinite(p[k]) or not lo<=p[k]<=hi:raise ValueError('coordinates')
    if not isinstance(p['timezone'],str) or not re.fullmatch(r'[A-Za-z_+\-/]{1,64}',p['timezone']):raise ValueError('timezone')
    try:ZoneInfo(p['timezone'])
    except (ZoneInfoNotFoundError,ValueError):raise ValueError('timezone_unknown') from None
    return out


class NoticeQueue:
    def __init__(self,*,clock=time.time,seconds=8):
        self.clock=clock;self.started=clock();self.seconds=seconds
        self.items=deque(maxlen=8);self.seen=OrderedDict()
    @property
    def pending_count(self):return len(self.items)
    def clear(self):self.items.clear()
    def add(self,source_id,title,body,*,created_at,app):
        now=self.clock()
        if (app!='telegram' or not isinstance(source_id,str) or not 1<=len(source_id)<=128
            or source_id in self.seen or type(created_at) not in (int,float)
            or not math.isfinite(created_at) or created_at<self.started or not -5<=now-created_at<=120):return False
        title=clean_text(title,24,96);body=clean_text(body,96,288)
        if not (title or body):return False
        self.seen[source_id]=None
        if len(self.seen)>256:self.seen.popitem(last=False)
        if len(self.items)==self.items.maxlen:
            del self.items[1]  # Keep the currently displayed notice stable.
        self.items.append(dict(id=(int(uuid.uuid4().hex[:8],16) or 1),app='telegram',title=title,
                               body=body,expires=created_at+120,display_until=None))
        return True
    def current(self):
        now=self.clock()
        while self.items:
            row=self.items[0]
            if row['expires']<=now or (row['display_until'] is not None and row['display_until']<=now):
                self.items.popleft();continue
            if row['display_until'] is None:row['display_until']=min(now+self.seconds,row['expires'])
            break
        if not self.items:return None
        row=self.items[0]
        return {k:v for k,v in row.items() if k not in ('expires','display_until')}|{'ttl_ms':max(1,int((row['display_until']-now)*1000))}


def normalise_weather(source,now):
    try:
        c=source['current'];u=source['current_units']
        if u['temperature_2m']!='°C' or u['apparent_temperature']!='°C' or u['wind_speed_10m']!='m/s':
            raise ValueError('weather_units')
        for k,lo,hi in [('temperature_2m',-90,65),('apparent_temperature',-100,80),('wind_speed_10m',0,150),('time',now-1800,now+300)]:
            if type(c[k]) not in (int,float) or not math.isfinite(c[k]) or not lo<=c[k]<=hi:raise ValueError('weather_values')
        code=c['weather_code']
        if type(code) is not int:raise ValueError('weather_code')
        if code in (0,1):kind,description='sun','Солнечно'
        elif code==2:kind,description='cloud','Облачно'
        elif code==3:kind,description='cloud','Пасмурно'
        elif code in (45,48):kind,description='cloud','Туман'
        elif code in (71,73,75,77,85,86):kind,description='snow','Снег'
        elif code in (51,53,55,56,57,61,63,65,66,67,80,81,82):kind,description='rain','Дождь'
        elif code in (95,96,99):kind,description='rain','Гроза'
        else:raise ValueError('weather_code')
        offset=source['utc_offset_seconds']
        if type(offset) is not int or not -43200<=offset<=50400:raise ValueError('weather_timezone')
        return dict(source='open_meteo',observed_at=c['time'],kind=kind,description=description,
                    temperature_c=c['temperature_2m'],feels_c=c['apparent_temperature'],wind_ms=c['wind_speed_10m'],
                    utc_offset_seconds=offset)
    except (KeyError,TypeError) as exc:raise ValueError('weather_schema') from exc


def weather_packet(value,now):
    if not isinstance(value,dict) or value.get('source')!='open_meteo':return None
    remaining=1800-(now-value['observed_at'])
    if not 0<remaining<=1805:return None
    return dict(description=value['description'],kind=value['kind'],temperature=f"{value['temperature_c']:g}°C",
                wind=f"{value['wind_ms']:g} м/с",feels=f"{value['feels_c']:g}°C",ttl_ms=min(1800000,int(remaining*1000)))
