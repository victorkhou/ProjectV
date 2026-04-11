"""
This reroutes from an URL to a python view-function/class.

The main web/urls.py includes these routes for all urls starting with `admin/`
(the `admin/` part should not be included again here).

"""

from django.urls import path

from evennia.web.admin.urls import urlpatterns as evennia_admin_urlpatterns

# Load custom admin registrations (unregister/re-register with game-specific columns)
import web.admin  # noqa: F401

# add patterns here
urlpatterns = []

# read by Django
urlpatterns = urlpatterns + evennia_admin_urlpatterns
