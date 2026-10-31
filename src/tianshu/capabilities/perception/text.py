"""Pure decoded representations. Sources keeps original ownership and revisions."""
import hashlib
from html.parser import HTMLParser

def content_hash(raw):return hashlib.sha256(raw).hexdigest()
class VisibleText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style","noscript"}:self.hidden+=1
        if tag in {"p","div","br","article","section","li","h1","h2","tr"}:self.parts.append("\n")
    def handle_endtag(self,tag):
        if tag in {"script","style","noscript"}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)
def decode(raw,media_type):
    text=raw.decode("utf-8-sig")
    if media_type=="text/html":
        parser=VisibleText();parser.feed(text);text="".join(parser.parts)
    return text

def blocks(text,groups=None):
    if groups is not None:raise ValueError("explicit_source_groups_are_owned_by_sources")
    return [{"spans":[[1,len(text.splitlines())]],"text":text}]
