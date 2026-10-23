"""SmallTV-Ultra V9.0.43 HTTP boundary; no discovery or arbitrary URLs."""
import gzip
import io
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class DeviceError(RuntimeError):
    pass


class DeviceHTTPError(DeviceError):
    def __init__(self, path, status):
        self.status = status
        super().__init__(f'HTTP {status}: {path.split("?")[0]}')


def validate_host(host):
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError as exc:
        raise DeviceError('Требуется явный LAN IPv4 без URL/порта') from exc
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if not any(address in ipaddress.ip_network(n) for n in networks):
        raise DeviceError('Разрешены только частные LAN IPv4')
    return str(address)


class _Listing(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paths = set()
        self.tables = self.closed = self.cells = 0
        self.bad_link = False
        self.row_open = False
        self.row_cells = self.row_links = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'table':
            self.tables += 1
        if tag == 'tr':
            if self.row_open:
                self.bad_link = True
            self.row_open = True
            self.row_cells = self.row_links = 0
        if tag == 'td':
            self.cells += 1
            self.row_cells += 1
        if tag == 'a':
            href = urllib.parse.unquote(values.get('href', ''))
            if re.fullmatch(r'/image/+[^/\\\x00-\x1f]+', href) and href.rsplit('/', 1)[1] not in ('.', '..'):
                self.paths.add('/image/' + href.rsplit('/', 1)[1])
                self.row_links += 1
            elif href and not href.endswith('/'):
                self.bad_link = True

    def handle_endtag(self, tag):
        if tag == 'tr':
            if self.row_cells and self.row_links != 1:
                self.bad_link = True
            self.row_open = False
        if tag == 'table':
            self.closed += 1


def parse_filelist(text):
    parser = _Listing()
    parser.feed(text)
    if (not parser.tables or parser.closed != parser.tables or parser.bad_link or parser.row_open
            or (parser.cells and not parser.paths)):
        raise DeviceError('Неизвестный или неполный формат каталога; загрузка запрещена')
    return parser.paths


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DeviceError('HTTP redirect запрещён')


class GeekMagic:
    def __init__(self, host=None):
        self.identity = validate_host(host)
        self.base = 'http://' + self.identity
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.upload_count = 0

    def _request(self, path, data=None, content_type=None):
        headers = {'Connection': 'close', 'User-Agent': 'MadyarMini-stage2'}
        if content_type:
            headers['Content-Type'] = content_type
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        limit = 2 * 1024 * 1024
        try:
            with self.opener.open(request, timeout=30 if data is not None else 5) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise DeviceError('Слишком большой ответ устройства')
                if response.headers.get('Content-Encoding', '').lower() == 'gzip':
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                        raw = stream.read(limit + 1)
                    if len(raw) > limit:
                        raise DeviceError('Слишком большой распакованный ответ')
                return raw
        except urllib.error.HTTPError as exc:
            raise DeviceHTTPError(path, exc.code) from exc
        except (OSError, urllib.error.URLError, EOFError) as exc:
            raise DeviceError(f'Не удалось выполнить запрос {path.split("?")[0]}') from exc

    def _json(self, path):
        try:
            value = json.loads(self._request(path))
            if not isinstance(value, dict):
                raise ValueError('not an object')
            return value
        except (ValueError, UnicodeError) as exc:
            raise DeviceError('Некорректный JSON: ' + path) from exc

    def snapshot(self):
        endpoints = {'version': '/v.json', 'app': '/app.json', 'album': '/album.json',
                     'brightness': '/brt.json', 'night': '/timebrt.json', 'delay': '/delay.json'}
        return {key: (self.optional_json(path) if key in ('brightness', 'delay') else self._json(path))
                for key, path in endpoints.items()}

    def optional_json(self, path):
        try:
            return self._json(path)
        except DeviceHTTPError as exc:
            if exc.status == 404:
                return {'unavailable': 'HTTP404'}
            raise

    def space(self):
        return self._json('/space.json')

    def theme(self):
        return self._json('/app.json').get('theme')

    def list_files(self):
        return parse_filelist(self._request('/filelist?dir=/image/').decode('utf-8', 'strict'))

    def download(self, path):
        if not re.fullmatch(r'/image/mm_[a-f0-9]{20}\.(?:jpg|gif)', path):
            raise DeviceError('Можно читать только собственный файл уведомления')
        return self._request(urllib.parse.quote(path, safe='/'))

    def upload(self, path, data):
        if not re.fullmatch(r'/image/mm_[a-f0-9]{20}\.(?:jpg|gif)', path):
            raise DeviceError('Недопустимое имя файла')
        filename = path.rsplit('/', 1)[1]
        boundary = 'MadyarMiniBoundary' + __import__('uuid').uuid4().hex
        mime = 'image/gif' if path.endswith('.gif') else 'image/jpeg'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()
        body += data + f'\r\n--{boundary}--\r\n'.encode()
        self.upload_count += 1
        self._request('/doUpload?dir=/image/', body, 'multipart/form-data; boundary=' + boundary)

    def _set(self, **params):
        reply = self._request('/set?' + urllib.parse.urlencode(params))
        if reply.strip().lower() != b'ok':
            raise DeviceError('Устройство не подтвердило изменение ответом OK')

    def set_theme(self, theme):
        if type(theme) is not int or theme not in range(1, 8):
            raise DeviceError('Неверная тема')
        self._set(theme=theme)

    def set_album(self, autoplay, interval):
        if type(autoplay) is not int or autoplay not in (0, 1) or type(interval) is not int or not 1 <= interval <= 86400:
            raise DeviceError('Неверные настройки альбома')
        self._set(i_i=interval, autoplay=autoplay)

    def show_image(self, path):
        if not re.fullmatch(r'/image/mm_[a-f0-9]{20}\.(?:jpg|gif)', path):
            raise DeviceError('Неверный путь изображения')
        self._set(img=path)
