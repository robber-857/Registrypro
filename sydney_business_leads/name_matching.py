"""Candidate ranking, deliberately separate from identity confirmation."""
import re
from difflib import SequenceMatcher


def words(name, address=''):
    name = name.casefold().replace('&', ' and ')
    name = re.split(r'\s+[-–@]\s*', name)[0]
    name = re.sub(r'\b(pty|ltd|limited|proprietary)\b', ' ', name)
    tokens = re.findall(r'[a-z0-9]+', name)
    for suburb in ('strathfield south', 'strathfield', 'burwood', 'homebush', 'lidcombe', 'greenacre'):
        suffix = suburb.split()
        if suburb in address.casefold() and tokens[-len(suffix):] == suffix:
            tokens = tokens[:-len(suffix)]
    return tokens


def similarity(first, second, address=''):
    a, b = words(first, address), words(second, address)
    if not a or not b:
        return 0.0
    # A centre and a tenant containing the centre's name are different businesses.
    if {'plaza', 'centre', 'center', 'mall'} & (set(a) | set(b)) and set(a) != set(b):
        return 0.0
    if ''.join(a)==''.join(b) or sorted(a) == sorted(b):
        return 1.0
    ratio = SequenceMatcher(None, ' '.join(sorted(a)), ' '.join(sorted(b))).ratio()
    overlap = len(set(a) & set(b)) / len(set(a) | set(b))
    return round(0.65 * ratio + 0.35 * overlap, 4)


def rank_names(info, row):
    ranked = [(similarity(name, row['name'], row.get('address', '')), name) for name in info['names']]
    if len(ranked) == 1 and re.search(r'\b(pty|ltd|limited)\b', info['legal_name'],re.I) and similarity(info['legal_name'],row['name'],row.get('address',''))>=0.82:
        ranked[0] = (max(ranked[0][0], similarity(info['legal_name'], row['name'], row.get('address', ''))), ranked[0][1])
    return sorted(ranked, key=lambda entry: (-entry[0], entry[1]))
