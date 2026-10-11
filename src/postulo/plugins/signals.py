"""What the plugin machinery announces, for whatever lives above it to hear (#887).

The plugins layer sits below the features, so it cannot import them to tell them a plugin has
gone. It sends a signal, and a feature that placed things on the person's behalf connects to it.
"""

from django.dispatch import Signal

#: A plugin was removed from the instance. ``name`` is its distribution name, the string a
#: plugin writes as ``source`` on what it places.
plugin_removed = Signal()
