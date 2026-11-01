import logging
import json
import math
import time
from collections.abc import Mapping
from email.utils import formatdate
from http.cookies import CookieError, Morsel, SimpleCookie

import aiohttp
from galaxy.api.errors import AuthenticationRequired, BackendError, TooManyRequests, TemporaryBlocked
from galaxy.http import create_client_session, handle_exception
from yarl import URL


INDIEGALA_URL = URL("https://www.indiegala.com/")
SESSION_STATUS_URL = "https://www.indiegala.com/ajax/user/get-data"
WEB_USER_AGENT = "Mozilla/5.0"
COOKIE_FORMAT = 1


def _is_indiegala_domain(domain):
    domain = domain.lstrip('.').lower()
    return domain == 'indiegala.com' or domain.endswith('.indiegala.com')


class CookieJar(aiohttp.CookieJar):
    # Inspired by https://github.com/TouwaStar/Galaxy_Plugin_Bethesda/blob/master/betty/http_client.py
    def __init__(self):
        super().__init__()
        self._cookies_updated_callback = None
        self._unscoped_cookie_names = set()
        self._restoring = False

    def set_cookies_updated_callback(self, callback):
        self._cookies_updated_callback = callback

    def update_cookies(self, cookies, url=URL()):
        hostname = url.raw_host
        if not hostname or not _is_indiegala_domain(hostname):
            return
        items = cookies.items() if isinstance(cookies, Mapping) else cookies
        accepted = []
        for name, value in items:
            if isinstance(value, Morsel):
                cookie = value.copy()
            else:
                parsed = SimpleCookie()
                parsed[name] = value
                cookie = parsed[name]
            domain = cookie['domain'].lstrip('.').lower() or hostname
            if (
                not _is_indiegala_domain(domain)
                or (hostname != domain and not hostname.endswith('.' + domain))
            ):
                continue
            if cookie['domain']:
                cookie['domain'] = domain
            path = cookie['path'] or url.path.rsplit('/', 1)[0] or '/'
            if (
                not self._restoring
                and name in self._unscoped_cookie_names
                and hostname == INDIEGALA_URL.raw_host
                and path == '/'
            ):
                # An inferred host-only copy must not override the server's
                # authoritative replacement or survive an explicit deletion.
                super().clear(lambda old: (
                    old.key == name
                    and old['domain'] == INDIEGALA_URL.raw_host
                    and old['path'] == '/'
                ))
                self._unscoped_cookie_names.discard(name)
            if cookie['max-age']:
                try:
                    expiry = min(time.time() + int(cookie['max-age']), self.MAX_TIME)
                    cookie['expires'] = formatdate(max(1, expiry), usegmt=True)
                except (ValueError, OverflowError):
                    pass
                # Persist an absolute deadline so a restart cannot extend it.
                cookie['max-age'] = ''
            accepted.append((name, cookie))
        super().update_cookies(accepted, url)
        if accepted and not self._restoring:
            self._notify_credentials()

    def _notify_credentials(self):
        if self._cookies_updated_callback:
            self._cookies_updated_callback(self.export_credentials())

    def export_credentials(self):
        records = []
        for cookie in self:
            records.append({
                'name': cookie.key,
                'value': cookie.value,
                'domain': cookie['domain'],
                'path': cookie['path'],
                'secure': bool(cookie['secure']),
                'http_only': bool(cookie['httponly']),
                'same_site': cookie['samesite'],
                'expires': cookie['expires'],
                'host_only': (cookie['domain'], cookie.key) in self.host_only_cookies,
                'scope_unknown': (
                    cookie.key in self._unscoped_cookie_names
                    and cookie['domain'] == INDIEGALA_URL.raw_host
                    and cookie['path'] == '/'
                    and (cookie['domain'], cookie.key) in self.host_only_cookies
                ),
            })
        return {'cookie_format': COOKIE_FORMAT, 'cookies': records}

    def restore_credentials(self, credentials):
        if not isinstance(credentials, dict):
            raise ValueError('Invalid IndieGala cookie storage')
        records = []
        if type(credentials.get('cookie_format')) is int:
            if credentials['cookie_format'] != COOKIE_FORMAT or not isinstance(credentials.get('cookies'), list):
                raise ValueError('Unsupported IndieGala cookie storage')
            for record in credentials['cookies']:
                if not isinstance(record, dict):
                    raise ValueError('Invalid IndieGala cookie entry')
                records.append(self._decode_cookie(record))
        else:
            for name, value in credentials.items():
                if not isinstance(name, str) or not name or not isinstance(value, str):
                    raise ValueError('Invalid IndieGala cookie entry')
                parsed = SimpleCookie()
                parsed[name] = value
                parsed[name]['path'] = '/'
                parsed[name]['secure'] = True
                records.append((parsed, INDIEGALA_URL, name))

        self.clear()
        self._restoring = True
        try:
            for cookies, origin, unscoped_name in records:
                if unscoped_name:
                    self._unscoped_cookie_names.add(unscoped_name)
                self.update_cookies(cookies, origin)
        finally:
            self._restoring = False
        self._notify_credentials()

    @staticmethod
    def _decode_cookie(record):
        name, value = record.get('name'), record.get('value')
        domain, path = record.get('domain'), record.get('path')
        if (
            not isinstance(name, str) or not name or not isinstance(value, str)
            or not isinstance(domain, str) or not _is_indiegala_domain(domain)
            or not isinstance(path, str) or not path.startswith('/')
        ):
            raise ValueError('Invalid IndieGala cookie entry')
        for flag in ('secure', 'http_only', 'host_only', 'scope_unknown'):
            if type(record.get(flag, False)) is not bool:
                raise ValueError('Invalid IndieGala cookie flag')
        for attribute in ('expires', 'same_site'):
            if not isinstance(record.get(attribute, ''), str):
                raise ValueError('Invalid IndieGala cookie attribute')
        domain = domain.lstrip('.').lower()
        host_only = record.get('host_only', False)
        unscoped = record.get('scope_unknown', False)
        if unscoped and (domain != INDIEGALA_URL.raw_host or path != '/' or not host_only):
            raise ValueError('Invalid IndieGala cookie scope')
        parsed = SimpleCookie()
        parsed[name] = value
        cookie = parsed[name]
        cookie['domain'] = '' if host_only else domain
        cookie['path'] = path
        cookie['secure'] = record.get('secure', False)
        cookie['httponly'] = record.get('http_only', False)
        cookie['samesite'] = record.get('same_site', '')
        cookie['expires'] = record.get('expires', '')
        return parsed, URL.build(scheme='https', host=domain), name if unscoped else None

    def import_browser_cookies(self, browser_cookies):
        for record in browser_cookies:
            if not isinstance(record, dict):
                continue
            name, value, domain = record.get('name'), record.get('value'), record.get('domain')
            if (
                not isinstance(name, str) or not name or not isinstance(value, str)
                or not isinstance(domain, str) or not _is_indiegala_domain(domain)
            ):
                continue
            parsed = SimpleCookie()
            parsed[name] = value
            cookie = parsed[name]
            cookie['domain'] = domain if domain.startswith('.') else ''
            cookie['path'] = record.get('path') or '/'
            cookie['secure'] = bool(record.get('secure'))
            cookie['httponly'] = bool(record.get('httpOnly'))
            cookie['samesite'] = record.get('sameSite') or ''
            expiry = record.get('expires')
            if isinstance(expiry, (int, float)) and not isinstance(expiry, bool) and math.isfinite(expiry) and expiry >= 0:
                cookie['expires'] = formatdate(max(1, min(expiry, self.MAX_TIME)), usegmt=True)
            self.update_cookies(parsed, URL.build(scheme='https', host=domain.lstrip('.')))

    def clear(self, predicate=None):
        super().clear(predicate)
        if predicate is None:
            self._unscoped_cookie_names.clear()


class HTTPClient(object):
    """Share refreshed cookies between account requests and website session checks."""
    def __init__(self, store_credentials):
        self.cookieJar = CookieJar()
        self.cookieJar.set_cookies_updated_callback(store_credentials)
        self.session = create_client_session(headers={
            'User-Agent': 'galaClient',
        },
            cookie_jar=self.cookieJar, raise_for_status=False)

    async def get(self, url):
        logging.debug('Calling HTTPClient.get with %s', url)
        return await self._request("GET", url)

    async def _request(self, method, url, **kwargs):
        with handle_exception():
            return await self._read_response(method, url, **kwargs)

    async def _read_response(self, method, url, **kwargs):
        async with self.session.request(method, url, **kwargs) as response:
            if response.status == 401:
                raise AuthenticationRequired()
            if response.status == 429:
                raise TooManyRequests()
            if response.status == 403:
                raise TemporaryBlocked("IndieGala denied the request; try again later")
            if response.status >= 400:
                raise BackendError(
                    "IndieGala request failed (HTTP {})".format(response.status),
                    {"http_status": response.status},
                )
            if response.status != 200:
                raise BackendError("IndieGala returned an unexpected HTTP status")
            text = await response.text()
        if '_Incapsula_Resource' in text:
            logging.debug('Incapsula challenge on request for %s', url)
            raise TemporaryBlocked("IndieGala requires a browser security check")
        if 'Profile locked' in text:
            logging.debug('IP check required on request for %s', url)
            raise TemporaryBlocked("IndieGala requires an IP security check")
        return text

    async def is_session_authenticated(self):
        """Confirm session expiry using the website's explicit login-status flag."""
        headers = {"User-Agent": WEB_USER_AGENT}
        # The website requires a CSRF cookie/header pair for this read-only POST.
        # Use the same cookie jar as the account request, never another profile.
        await self._request(
            "GET", str(INDIEGALA_URL), headers=headers, allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=12),
        )
        csrf_cookie = self.cookieJar.filter_cookies(INDIEGALA_URL).get("csrftoken")
        if csrf_cookie is None or not csrf_cookie.value:
            raise BackendError("IndieGala session status could not be verified")
        headers.update({
            "Referer": str(INDIEGALA_URL),
            "X-CSRF-Token": csrf_cookie.value,
            "X-CSRFToken": csrf_cookie.value,
        })
        text = await self._request(
            "POST", SESSION_STATUS_URL, headers=headers, json={},
            allow_redirects=False, timeout=aiohttp.ClientTimeout(total=12),
        )
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as error:
            raise BackendError("IndieGala returned an invalid session status") from error
        status = data.get("user_is_logged") if isinstance(data, dict) else None
        if not isinstance(status, bool):
            raise BackendError("IndieGala returned an incomplete session status")
        return status

    def update_cookies(self, cookies):
        try:
            self.cookieJar.restore_credentials(cookies)
        except (CookieError, ValueError, TypeError) as error:
            raise AuthenticationRequired('Stored IndieGala cookies could not be restored') from error

    def clear_cookies(self):
        self.cookieJar.clear()

    async def close(self):
        await self.session.close()
