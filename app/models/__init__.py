# app/models/__init__.py
from app.db.base import Base  # noqa: F401

from . import user            # noqa: F401
from . import company         # noqa: F401
from . import package         # noqa: F401
from . import company_package # noqa: F401
from . import invite          # noqa: F401
from . import password_reset  # noqa: F401

from . import ai_system       # noqa: F401
from . import admin_assignment  # noqa: F401
from . import system_assignment # noqa: F401

from . import ai_assessment   # noqa: F401

# jedino ovo za snapshot *modele*
from . import task_stats      # noqa: F401
