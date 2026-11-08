"""Sample concurrency-oriented DB test using two real connections.

Uses pytest-django's transactional DB setup (equivalent surface to
TransactionTestCase) so committed rows are visible across connections.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import connections

pytestmark = pytest.mark.django_db(transaction=True)


def test_second_connection_sees_committed_row():
    User = get_user_model()
    User.objects.create_user(
        username='synth_concurrency_user',
        password='unused-synthetic-password',
    )

    other = connections.create_connection('default')
    try:
        with other.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*) FROM auth_user WHERE username = %s',
                ['synth_concurrency_user'],
            )
            count = cursor.fetchone()[0]
    finally:
        other.close()

    assert count == 1
