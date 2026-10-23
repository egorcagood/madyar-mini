"""Documented sources only; never read Codex transcripts or Telegram databases."""
import json
import re
import time
import urllib.parse
import urllib.request
from .codex_events import hook_event
from .device import _NoRedirect
from .integration_data import clean_text, normalise_weather, default_settings, validate_settings


INPUT_TOOLS={'request_user_input','request_user_input_async'}

def project_hook(payload):
    if not isinstance(payload,dict) or payload.get('agent_id') or payload.get('agent_type'):return None
    event=hook_event(payload)
    if event is None:return None
    tool=payload.get('tool_name','')
    if not isinstance(tool,str) or not re.fullmatch(r'[A-Za-z0-9_:.]{0,160}',tool):tool=''
    use=payload.get('tool_use_id')
    if not isinstance(use,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',use):use=None
    # Async asking returns immediately: its PostToolUse is not evidence of a user's answer.
    if payload['hook_event_name']=='PreToolUse' and tool in INPUT_TOOLS:event['kind']='input_required'
    return dict(event=event,hook=payload['hook_event_name'],tool_name=tool,tool_use_id=use,
                blocking_input=tool=='request_user_input')


class WeatherClient:
    def __init__(self,*,fetch=None):
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
        self.fetch=fetch or self._fetch
    def _fetch(self,url):
        if not url.startswith(('https://api.open-meteo.com/v1/forecast?',
                               'https://geocoding-api.open-meteo.com/v1/search?')):
            raise ValueError('provider_url')
        request=urllib.request.Request(url,headers={'User-Agent':'MadyarMini/1.0-local','Accept':'application/json'})
        with self.opener.open(request,timeout=8) as response:
            raw=response.read(131073)
        if len(raw)>131072:raise ValueError('weather_response_limit')
        data=json.loads(raw)
        if not isinstance(data,dict):raise ValueError('weather_response')
        return data
    def search(self,query):
        if (not isinstance(query,str) or not 2<=len(query)<=40 or clean_text(query,40,120)!=query
            or any(c in query for c in '/<>:')):raise ValueError('city_query')
        query='Saint Petersburg' if query.casefold()=='питер' else query
        url='https://geocoding-api.open-meteo.com/v1/search?'+urllib.parse.urlencode(dict(name=query,count=8,language='ru',format='json'))
        data=self.fetch(url);results=[]
        for row in data.get('results',[])[:8]:
            try:
                place={'name':row['name'],'country':row['country_code'],'latitude':row['latitude'],
                       'longitude':row['longitude'],'timezone':row['timezone']}
                settings=default_settings();settings['place']=place
                results.append(validate_settings(settings)['place'])
            except (KeyError,TypeError,ValueError):continue
        return results
    def current(self,place,*,now=None):
        settings=default_settings();settings['place']=place;place=validate_settings(settings)['place']
        url='https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode(dict(
            latitude=place['latitude'],longitude=place['longitude'],
            current='temperature_2m,apparent_temperature,weather_code,wind_speed_10m',
            temperature_unit='celsius',wind_speed_unit='ms',timeformat='unixtime',timezone=place['timezone']))
        return normalise_weather(self.fetch(url),time.time() if now is None else now)
