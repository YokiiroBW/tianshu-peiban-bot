"""Portable HTML parser replacing the upstream native html5-parser binding."""
from lxml.html import HTMLParser, fromstring

def parse(content, **kwargs):
    return fromstring(content, parser=HTMLParser(no_network=True))
