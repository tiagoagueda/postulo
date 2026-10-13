"""The week starts on a Monday in Kazakhstan; Django has no format module for Kazakh, so it
fell through to the settings and to Sunday (#269).
"""

FIRST_DAY_OF_WEEK = 1
