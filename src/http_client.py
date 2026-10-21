import logging
import json

import aiohttp
from galaxy.api.errors import AuthenticationRequired, BackendError, TooManyRequests, TemporaryBlocked
from galaxy.http import create_client_session
from yarl import URL


INDIEGALA_URL = URL("https://www.indiegala.com/")
SESSION_STATUS_URL = "https://www.indiegala.com/ajax/user/get-data"
WEB_USER_AGENT = "Mozilla/5.0"


class CookieJar(aiohttp.CookieJar):
    # Inspired by https://github.com/TouwaStar/Galaxy_Plugin_Bethesda/blob/master/betty/http_client.py
    def __init__(self):
        super().__init__()
        self._cookies_updated_callback = None

    def set_cookies_updated_callback(self, callback):
        self._cookies_updated_callback = callback

    def update_cookies(self, cookies, url=URL()):
        super().update_cookies(cookies, url)
        if cookies and self._cookies_updated_callback:
            all_cookies = {cookie.key: cookie.value for cookie in self}
            self._cookies_updated_callback(all_cookies)


class HTTPClient(object):
    """
    Intended to store and track cookies and update them on each request
    """
    def __init__(self, store_credentials):
        self.cookieJar = CookieJar()
        self.cookieJar.set_cookies_updated_callback(store_credentials)
        self.session = create_client_session(headers={
            'User-Agent': 'galaClient',
        },
            cookie_jar=self.cookieJar, raise_for_status=False)

    async def get(self, url):
        """
        returns the url and updates the cookies
        :param url:
        :return:
        """
        logging.debug('Calling HTTPClient.get with %s', url)
        return await self._request("GET", url)

    async def _request(self, method, url, **kwargs):
        async with self.session.request(method, url, **kwargs) as response:
            if response.status == 401:
                raise AuthenticationRequired()
            if response.status == 429:
                raise TooManyRequests()
            if response.status == 403:
                raise TemporaryBlocked("IndieGala denied the request; try again later")
            if response.status >= 400:
                raise BackendError("IndieGala request failed (HTTP {})".format(response.status))
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
        self.cookieJar.update_cookies(cookies, INDIEGALA_URL)

    def clear_cookies(self):
        self.cookieJar.clear()

    async def close(self):
        await self.session.close()
