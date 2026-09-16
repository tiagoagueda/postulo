"""Format definitions Postulo supplies itself, for locales Django gets wrong for us.

``FORMAT_MODULE_PATH`` points here. Django looks for ``<locale>/formats.py`` under each
path in turn and falls through to its own ``django.conf.locale`` when it finds nothing, so
a locale with no directory here is untouched. Only names Django already knows belong in
these files: ``get_format`` returns an unrecognised name verbatim, which is how an invented
format ends up printed on the page in whichever language forgot to define it.
"""
