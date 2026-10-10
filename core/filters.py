"""HTTrack-style scan rules:  +*.png  -*doubleclick.net*  (last matching rule wins)."""
import fnmatch
import re
from typing import Optional


class ScanRules:
    def __init__(self, text: str = ''):
        self.rules = []
        for tok in re.split(r'\s+', (text or '').strip()):
            if not tok:
                continue
            allow = tok[0] != '-'
            pat = tok[1:] if tok[0] in '+-' else tok
            if pat:
                self.rules.append((allow, pat.lower()))

    def decide(self, url: str) -> Optional[bool]:
        u = url.lower()
        nos = u.split('://', 1)[-1]
        result = None
        for allow, pat in self.rules:
            if fnmatch.fnmatchcase(u, pat) or fnmatch.fnmatchcase(nos, pat):
                result = allow
        return result

    def allowed(self, url: str) -> bool:
        d = self.decide(url)
        return True if d is None else d
