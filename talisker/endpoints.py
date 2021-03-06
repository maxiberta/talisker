# Copyright (C) 2016- Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import

from builtins import *  # noqa

import os
import sys
import collections
import functools
from ipaddress import ip_address, ip_network
from werkzeug.wrappers import Request, Response
from talisker import revision, util


__all__ = []


class TestException(Exception):
    pass


NETWORKS = []
_loaded = False


def force_unicode(s):
    if isinstance(s, bytes):
        return s.decode('utf8')
    return s


def load_networks():
    networks = os.environ.get('TALISKER_NETWORKS', '').split(' ')
    return [ip_network(force_unicode(n)) for n in networks if n]


def private(f):
    """Only allow approved source addresses."""
    @functools.wraps(f)
    def wrapper(self, request):
        global NETWORKS, _loaded
        if not _loaded:
            NETWORKS = load_networks()
            _loaded = True
        if not request.access_route:
            # no client ip
            return Response(status='403')
        ip_str = request.access_route[0]
        if isinstance(ip_str, bytes):
            ip_str = ip_str.decode('utf8')
        ip = ip_address(ip_str)
        if ip.is_loopback or any(ip in network for network in NETWORKS):
            return f(self, request)
        else:
            return Response(status='403')
    return wrapper


class StandardEndpointMiddleware(object):
    """WSGI middleware to provide a standard set of endpoints for a service"""

    _ok_response = None

    urlmap = collections.OrderedDict((
        ('/', 'index'),
        ('/index', 'index'),
        ('/check', 'check'),
        ('/info', 'info'),
        ('/metrics', None),
        ('/ping', 'ping'),
        ('/error', 'error'),
        ('/test/sentry', 'error'),
        ('/test/statsd', 'test_statsd'),
        ('/test/prometheus', None),
        ))

    @property
    def _ok(self):
        if self._ok_response is None:
            self._ok_response = Response(str(revision.get()))
        return self._ok_response

    def __init__(self, app, namespace='_status'):
        self.app = app
        self.namespace = namespace
        self.prefix = '/' + namespace
        # Publish /metrics only if prometheus_client is available
        if util.pkg_is_installed('prometheus-client'):
            self.urlmap['/metrics'] = 'metrics'
            self.urlmap['/test/prometheus'] = 'test_prometheus'

    def __call__(self, environ, start_response):
        request = Request(environ)
        if request.path.startswith(self.prefix):
            method = request.path[len(self.prefix):]
            if method == '':
                # no trailing /
                start_response('302', [('location', self.prefix + '/')])
                return ''
            try:
                funcname = self.urlmap[method]
                func = getattr(self, funcname)
            except (KeyError, AttributeError):
                response = Response(status=404)
            else:
                response = func(request)

            return response(environ, start_response)
        else:
            return self.app(environ, start_response)

    def index(self, request):
        methods = []
        item = '<li><a href="{0}"/>{1}</a> - {2}</li>'
        for url, funcname in self.urlmap.items():
            if funcname and funcname != 'index':
                func = getattr(self, funcname)
                methods.append(
                    item.format(self.prefix + url, funcname, func.__doc__))
        return Response(
            '<ul>' + '\n'.join(methods) + '<ul>', mimetype='text/html')

    def ping(self, request):
        """HAProxy status check"""
        return self._ok

    def check(self, request):
        """Nagios health check"""
        start = {}

        def nagios_start(status, headers, exc_info=None):
            # save status for inspection
            start['status'] = status
            start['headers'] = headers
            if exc_info:
                start['exc'] = sys.exc_info()

        response = self.app(request.environ, nagios_start)
        if not start:
            # nagios_start has not yet been called
            if isinstance(response, collections.Iterable):
                # force evaluation
                response = b''.join(response)

        if 'exc' in start:
            return Response('error', status=500)
        elif start.get('status', '').startswith('404'):
            # app does not provide /_status/nagios endpoint
            return self._ok
        else:
            # return app's response
            return Response(response,
                            status=start.get('status', 200),
                            headers=start.get('headers', []))

    @private
    def error(self, request):
        """Raise a TestError for testing"""
        raise TestException('this is a test, ignore')

    @private
    def test_statsd(self, request):
        """Increment statsd metric for testing"""
        statsd = request.environ['statsd']
        statsd.incr('test')
        return Response('Incremented {}.test'.format(statsd._prefix))

    @private
    def test_prometheus(self, request):
        """Increment prometheus metric for testing"""
        if not util.pkg_is_installed('prometheus-client'):
            return Response('Not Supported', status=501)

        if not hasattr(self, 'test_counter'):
            import prometheus_client
            self.test_counter = prometheus_client.Counter('test', 'test')
        self.test_counter.inc()
        return Response('Incremented test counter')

    @private
    def info(self, request):
        return Response('Not Implemented', status=501)

    @private
    def metrics(self, request):
        """Endpoint exposing Prometheus metrics"""
        if not util.pkg_is_installed('prometheus-client'):
            return Response('Not Supported', status=501)

        # Importing this too early would break multiprocess metrics
        from prometheus_client import (
            CONTENT_TYPE_LATEST,
            CollectorRegistry,
            REGISTRY,
            generate_latest,
            multiprocess,
        )

        if 'prometheus_multiproc_dir' in os.environ:
            # prometheus_client is running in multiprocess mode.
            # Use a custom registry, as the global one includes custom
            # collectors which are not supported in this mode
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
        else:
            if request.environ.get('wsgi.multiprocess', False):
                return Response(
                    'Not Supported: running in multiprocess mode but '
                    '`prometheus_multiproc_dir` envvar not set',
                    status=501)

            # prometheus_client is running in single process mode.
            # Use the global registry (includes CPU and RAM collectors)
            registry = REGISTRY

        data = generate_latest(registry)
        return Response(data, status=200, mimetype=CONTENT_TYPE_LATEST)
