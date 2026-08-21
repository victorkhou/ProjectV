"""
Search / multimatch result handling — NOT wired.

Evennia's own ``evennia.utils.utils.at_search_result`` handles search results
(the default ``SEARCH_AT_RESULT``). To customize how no-match and multimatch
results are reported, implement ``at_search_result(matches, caller, query="",
quiet=False, **kwargs)`` here and point the setting at it::

    SEARCH_AT_RESULT = "server.conf.at_search.at_search_result"

It must return a single object or ``None``, having already reported any error.
"""
