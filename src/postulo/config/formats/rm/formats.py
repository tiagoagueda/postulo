"""The week starts on a Monday in Switzerland; Django has no locale for Romansh at all, so it
fell through to the settings and to Sunday (#269).
"""

FIRST_DAY_OF_WEEK = 1
