"""Normalize unambiguous large spoken numbers before machine translation.

This is deterministic, language-rule-based parsing, not a second model. Restrict
automatic mode to large numbers to avoid homographs such as English 'four' and
French 'four' (oven). Ambiguous overlapping parses are left untouched.
"""
from __future__ import annotations
from collections import defaultdict
import re
from text_to_num import Token, find_numbers

NUMBER_LANGUAGES = {'English':'en', 'French':'fr', 'Italian':'it', 'Spanish':'es',
                    'German':'de', 'Portuguese':'pt', 'Dutch':'nl'}


class NumberToken(Token):
    def __init__(self, value): self.value = value
    def text(self): return self.value


def normalize_large_numbers(text: str, language: str = '') -> str:
    spans = list(re.finditer(r'[^\W\d_]+(?:[-’\'][^\W\d_]+)*|\d+(?:[.,]\d+)*|[^\w\s]', text, re.UNICODE))
    tokens = [NumberToken(match.group()) for match in spans]
    if not tokens:
        return text
    selected = NUMBER_LANGUAGES.get(language)
    languages = [selected] if selected else list(NUMBER_LANGUAGES.values()) if not language else []
    parses = defaultdict(set)
    for code in languages:
        for number in find_numbers(tokens, code, threshold=1000):
            if number.is_ordinal or number.value < 1000:
                continue
            start, end = spans[number.start].start(), spans[number.end-1].end()
            if not any(char.isalpha() for char in text[start:end]):
                continue
            # An approximate magnitude is not an exact number: "thousands of miles"
            # must remain "数千英里", never become "1000 英里".
            first = spans[number.start].group().casefold()
            if first in {'hundreds','thousands','millions','billions','trillions',
                         'milliers','milliards','migliaia','milioni','miliardi','miles',
                         'millones','milhares','milhões','miljoenen','miljarden','tausende'}:
                continue
            parses[(start, end)].add(number.text)
    # Prefer the complete long number over a shorter prefix from another grammar.
    accepted = []
    for (start, end), values in sorted(parses.items(), key=lambda item: (-(item[0][1]-item[0][0]), item[0][0])):
        if len(values) != 1 or any(start < old_end and end > old_start for old_start, old_end, _ in accepted):
            continue
        accepted.append((start, end, next(iter(values))))
    for start, end, replacement in sorted(accepted, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text
