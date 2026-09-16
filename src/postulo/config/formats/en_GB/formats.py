"""British English, with the clock Postulo has always shown it on.

Moving the templates off literal date formats (#225) was about order and the clock: the
reader's language decides whether the year comes first, and whether an interview is at 14:30
or at 2.30 p.m. Django's ``en_GB`` answers the second question with ``P`` -- "2.30 p.m." --
where every template Postulo has ever shipped wrote 14:30, and a source language is a poor
place to start changing what the interface says, so that one answer is replaced here.

Nothing else is. ``DATE_FORMAT`` in Django's ``en_GB`` is already ``j M Y``, the literal
those templates spelled out; ``MONTH_DAY_FORMAT`` and ``YEAR_MONTH_FORMAT`` spell the month
out where the templates abbreviated it, and that is Django's judgement about English rather
than a bug -- the same judgement is what makes German say "16. September", so a layout that
cannot hold it was going to break in thirty-seven languages anyway.

Every other language keeps Django's own definitions: a directory under
``FORMAT_MODULE_PATH`` applies to the locale it is named after and to nothing else.
"""

TIME_FORMAT = "H:i"
DATETIME_FORMAT = "j M Y, H:i"
SHORT_DATETIME_FORMAT = "d/m/Y H:i"
