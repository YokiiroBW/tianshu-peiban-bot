from LegadoParser2.FormatUtils import Fmt
from LegadoParser2.RuleUrl.Url import parseUrl, getContent
from LegadoParser2.RuleJs.JS import EvalJs
from LegadoParser2.RuleEval import getString
from tianshu.capabilities.contracts import require
from tianshu.capabilities.media.books.engine_pages import read_pages
from tianshu.capabilities.media.books.readability import check_content
from LegadoParser2.config import DEBUG_MODE
try:
    from LegadoParser2.fontutils import checkPUA, fixPUAStr, collectPUAChars
except ImportError:
    pass


def getChapterContent(compiledBookSource, url, variables, nextChapterUrl=''):
    # trimBookSource(compiledBookSource)
    ruleContent = compiledBookSource['ruleContent']
    evalJs = EvalJs(compiledBookSource)
    evalJs.loadVariables(variables)
    if compiledBookSource.get('header'):
        headers = compiledBookSource['header']
    else:
        headers = ''
    urlObj = parseUrl(url, evalJs, headers=headers)
    if ruleContent.get('webJs'):
        urlObj['webJs'] = ruleContent['webJs']
    evalJs.set('baseUrl', url)
    content, __ = getContent(urlObj)
    return parseContent(compiledBookSource, urlObj, content.strip(), evalJs, nextChapterUrl=nextChapterUrl)


def parseContent(bS, urlObj, content, evalJs, **kwargs):
    ruleContent = bS['ruleContent']
    nextChapterUrl = kwargs.get('nextChapterUrl')
    # if not ruleContent:
    #     return {}

    _content = content

    chapterContent = {'content': ''}

    def parseCt(content):

        if ruleContent.get('content'):
            text = getString(
                content, ruleContent['content'], evalJs, rawContent=_content)
            if not text.strip():
                check_content(content)
            require(text.strip(), 'book_content_empty', 502)
            check_content(text)
            chapterContent['content'] += text + '\n'

    for page, body in read_pages(urlObj, _content, evalJs, ruleContent.get('nextContentUrl'),
                                 headers=bS.get('header', ''), stop_urls=(nextChapterUrl,)):
        urlObj, _content = page, body
        parseCt(body)

    if chapterContent['content']:
        chapterContent['content'] = Fmt.html(chapterContent['content'])

    if ruleContent.get('replaceRegex'):
        chapterContent['content'] = getString(
            chapterContent['content'], ruleContent['replaceRegex'], evalJs, rawContent=_content)
    try:
        if checkPUA(chapterContent['content']):
            if DEBUG_MODE:
                print('parseContent 发现PUA字符，尝试OCR修复')
            PUAChars = collectPUAChars(chapterContent['content'])
            chapterContent['content'] = fixPUAStr(
                chapterContent['content'], urlObj['allFontFaceUrl'], PUAChars)
    except NameError:
        pass
    except Exception:
        if DEBUG_MODE:
            print('parseContent OCR修复出错，已取消进行')

    return chapterContent
