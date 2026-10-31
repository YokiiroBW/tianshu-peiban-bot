"""Bounded ZIP extraction for the upstream JavaScript helper API."""
from LegadoParser2.HttpRequset2 import req
from LegadoParser2.RuleCompile import getRuleObj
import io
import zipfile

def getZipStringContent(url, path):
    from tianshu.capabilities.contracts import require
    data = req(url)[2].content if url.startswith(('http://', 'https://')) else bytes.fromhex(url)
    require(len(data) <= 8 * 1024 * 1024, "book_response_too_large", 413)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        item = archive.getinfo(path)
        require(item.file_size <= 2 * 1024 * 1024, "book_response_too_large", 413)
        with archive.open(item) as stream:
            content = stream.read(2 * 1024 * 1024 + 1)
        require(len(content) <= 2 * 1024 * 1024, "book_response_too_large", 413)
        try:
            return content.decode('utf-8-sig')
        except UnicodeError:
            return content.decode('gb18030')

_cache = {}
_MAXCACHE = 512


def getStringJs(content, evalJs, rule, isUrl=False):
    from LegadoParser2.RuleEval import getString, getStrings
    rulesObj = None
    try:
        rulesObj = _cache[rule]
    except KeyError:
        pass
    if not rulesObj:
        rulesObj = getRuleObj(rule)
        if len(_cache) >= _MAXCACHE:
            # 删除最旧的缓存
            try:
                del _cache[next(iter(_cache))]
            except (StopIteration, RuntimeError, KeyError):
                pass
        _cache[rule] = rulesObj
    if isUrl:
        texts = getStrings(content, rulesObj, evalJs)
        if texts:
            return texts[0]
        else:
            return ''
    else:
        text = getString(content, rulesObj, evalJs)
        return text
