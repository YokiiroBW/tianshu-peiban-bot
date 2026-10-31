from LegadoParser2.RuleUrl.Url import parseUrl, getContent, urljoin
from LegadoParser2.RuleJs.JS import EvalJs
from LegadoParser2.RuleEval import getElements, getString
from LegadoParser2.utils import validateFlag
from tianshu.capabilities.media.books.engine_pages import read_pages


# from lxml.etree import HTML

def getChapterList(compiledBookSource, url, variables):
    # trimBookSource(compiledBookSource)
    evalJs = EvalJs(compiledBookSource)
    evalJs.loadVariables(variables)
    if compiledBookSource.get('header', None):
        headers = compiledBookSource['header']
    else:
        headers = ''
    urlObj = parseUrl(url, evalJs, headers=headers)
    evalJs.set('baseUrl', url)
    content, __ = getContent(urlObj)
    return parseChapterList(compiledBookSource, urlObj, content.strip(), evalJs)


def parseChapterList(bS, urlObj, content, evalJs: EvalJs):
    ruleToc = bS['ruleToc']

    # if not ruleToc:
    #     return []

    chapterList = []
    chapterUrlSet = set()

    def parseCL(content):

        elements = getElements(content, ruleToc['chapterList'], evalJs)

        for e in elements:
            chapter = {}
            if ruleToc.get('chapterName', None):
                chapter['name'] = getString(e, ruleToc['chapterName'], evalJs)
            if ruleToc.get('chapterUrl', None):
                chapter['url'] = getString(e, ruleToc['chapterUrl'], evalJs)
                if chapter['url']:
                    chapter['url'] = urljoin(urlObj['finalurl'], chapter['url'])
                else:
                    chapter['url'] = urlObj['rawUrl']
            else:
                chapter['url'] = urlObj['rawUrl']
            if not chapter.get('url'):
                chapter['url'] = urlObj['rawUrl']
            chapterUrlSet.add(chapter['url'])
            if ruleToc.get('isPay', None):
                chapter['isPay'] = getString(e, ruleToc['isPay'], evalJs)
                chapter['isPay'] = validateFlag(chapter['isPay'])
            if ruleToc.get('isVip', None):
                chapter['isVip'] = getString(e, ruleToc['isVip'], evalJs)
                chapter['isVip'] = validateFlag(chapter['isVip'])
            if ruleToc.get('isVolume', None):
                chapter['isVolume'] = getString(e, ruleToc['isVolume'], evalJs)
                chapter['isVolume'] = validateFlag(chapter['isVolume'])
            if ruleToc.get('updateTime', None):
                chapter['updateTime'] = getString(e, ruleToc['updateTime'], evalJs)
            chapter['variables'] = evalJs.dumpVariables()
            if chapter['name']:
                chapterList.append(chapter)

    for page, body in read_pages(urlObj, content, evalJs, ruleToc.get('nextTocUrl'),
                                 headers=bS.get('header', ''), stop_urls=chapterUrlSet):
        urlObj = page
        parseCL(body)
    chapterList = removeLatestChapter(chapterList)

    return chapterList


# 移除章节列表中上方的最新章节
def removeLatestChapter(chapterList):
    if not chapterList:
        return chapterList

    length = len(chapterList)
    for idx in range(length):
        if chapterList[idx]['url'] != chapterList[length - idx - 1]['url'] or idx + 1 > length / 2:
            break
    return chapterList[idx:]
