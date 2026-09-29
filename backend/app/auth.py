"""Identity resolution is request-level, never taken from session configuration."""
from typing import Protocol
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .models import User


class AuthProvider(Protocol):
    def get_current_user(self, db, request) -> User: ...


class DevelopmentAuthProvider:
    def __init__(self, username='development'):
        self.username = username

    def get_current_user(self, db, request=None):
        user = db.scalar(select(User).where(User.username == self.username))
        if user is None:
            try:
                with db.begin_nested():
                    user = User(username=self.username, role='user')
                    db.add(user)
                    db.flush()
            except IntegrityError:
                user = db.scalar(select(User).where(User.username == self.username))
        return user


class AuthorizationPolicy:
    def can_access(self, current_user, resource):
        return resource.user_id == current_user.id


AUTH_PROVIDERS = {'development': lambda settings: DevelopmentAuthProvider(settings.development_username)}
