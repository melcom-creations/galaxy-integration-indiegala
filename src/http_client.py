import logging

import aiohttp
from galaxy.api.errors import AuthenticationRequired, BackendError, TooManyRequests, TemporaryBlocked
from galaxy.http import create_client_session
from yarl import URL


INDIEGALA_URL = URL("https://www.indiegala.com/")


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
            cookie_jar=self.cookieJar)

    async def get(self, url):
        """
        returns the url and updates the cookies
        :param url:
        :return:
        """
        logging.debug('Calling HTTPClient.get with %s', url)
        async with self.session.get(url) as response:
            if response.status == 401:
                raise AuthenticationRequired()
            if response.status == 429:
                raise TooManyRequests()
            if response.status == 403:
                raise TemporaryBlocked("IndieGala denied the request; try again later")
            if response.status >= 400:
                raise BackendError("IndieGala request failed (HTTP {})".format(response.status))
            text = await response.text()
        if '_Incapsula_Resource' in text:
            logging.debug('Incapsula challenge on request for %s', url)
            raise TemporaryBlocked("IndieGala requires a browser security check")
        if 'Profile locked' in text:
            logging.debug('IP check required on request for %s', url)
            raise TemporaryBlocked("IndieGala requires an IP security check")
        return text

    def update_cookies(self, cookies):
        self.cookieJar.update_cookies(cookies, INDIEGALA_URL)

    def clear_cookies(self):
        self.cookieJar.clear()

    async def close(self):
        await self.session.close()
