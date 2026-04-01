import pytest
from django.contrib.auth.models import User


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass123", email="test@example.com")


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(username="admin", password="adminpass123", email="admin@example.com")


@pytest.fixture
def client_logged_in(client, user):
    client.login(username="testuser", password="testpass123")
    return client
