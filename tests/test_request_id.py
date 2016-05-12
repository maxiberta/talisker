from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import

from future import standard_library
standard_library.install_aliases()
from builtins import *  # noqa

import uuid
from wsgiref.util import setup_testing_defaults

from py.test import fixture
import mock

from .fixtures import clean_up_context  # noqa
from talisker import request_id
from talisker.request_context import request_context


@fixture
def environ():
    env = {}
    setup_testing_defaults(env)
    return env


@fixture
def start_response():
    return lambda: None


@fixture
def id():
    return str(uuid.uuid4()).encode('utf8')


def make_assert_app(id):
    def app(environ, start_response):
        assert environ[b'REQUEST_ID'] == id
        assert request_id.get() == id
        assert request_context.extra['request_id'] == id
    return app


def test_middleware_with_id(environ, start_response, id):
    environ[b'HTTP_X_REQUEST_ID'] = id
    app = make_assert_app(id)
    middleware = request_id.RequestIdMiddleware(app)
    middleware(environ, start_response)


def test_middleware_without_id(environ, start_response, id):
    app = make_assert_app(id)
    middleware = request_id.RequestIdMiddleware(app)
    with mock.patch('talisker.request_id.generate') as mock_id:
        mock_id.return_value = id
        middleware(environ, start_response)


def test_middleware_alt_header(environ, start_response, id):
    environ[b'HTTP_X_ALTERNATE'] = id
    app = make_assert_app(id)
    middleware = request_id.RequestIdMiddleware(app, 'X-Alternate')
    middleware(environ, start_response)


def test_decorator(id):

    @request_id.decorator(lambda: id)
    def test():
        assert request_id.get() == id

    test()