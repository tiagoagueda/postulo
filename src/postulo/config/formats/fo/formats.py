"""The week starts on a Monday in the Faroe Islands; Django has no locale for Faroese at all,
so it fell through to the settings and to Sunday (#269).
"""

FIRST_DAY_OF_WEEK = 1
