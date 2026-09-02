"""Declarative base for the ORM layer.

`app.db.models` is imported at the bottom of this module (not the top) so that every
domain table registers itself on `Base.metadata` as a side effect of importing this module
once — the single place Alembic's `env.py` and anything else needing `Base.metadata` should
import from, without also having to remember to import `app.db.models` separately.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()

from app.db import models  # noqa: E402,F401  (see docstring — must follow Base's definition)
