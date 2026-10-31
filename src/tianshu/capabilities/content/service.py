"""Read public web/forum/Bilibili content; searches remain unread candidates."""
import asyncio
import json
import time
from datetime import UTC,datetime
from urllib.parse import urlsplit,urljoin
from uuid import uuid4
import aiohttp
from bs4 import BeautifulSoup
from tianshu.types import SourceRef,Receipt,Coverage
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition,Availability
from ..contracts import Fault,canonical,digest
from ..content_discovery.service import Discovery
from ..content_discovery.media import MediaCatalog
from ..content_discovery.candidates import public_url,candidate

class Content:
    definition=CapabilityDefinition("content.read","阅读内容","获取真实网页或论坛正文，保留原URL/作者/楼层与分页。",{"type":"object","properties":{"url":{"type":"string"},"mode":{"enum":["research","explore","continue"]}},"required":["url"]},{"type":"object"},"read")
    def __init__(self,configuration,sources,authority,media=None,browser=None):
        self.config,self.sources,self.authority,self.media,self.browser=configuration,sources,authority,media,browser
        self.discovery=Discovery(MediaCatalog(media) if media else None)
    async def availability(self,context):return Availability(True,self.sources is not None,reason=None if self.sources else "source_port_unavailable")
    async def fetch(self,url,*,accept="text/html,application/json,text/plain",maximum=4*1024*1024,headers=None):
        public_url(url)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20,connect=10)) as session:
            for _ in range(4):
                async with session.get(url,headers={"Accept":accept,**(headers or {})},allow_redirects=False) as response:
                    if response.status in {301,302,303,307,308}:
                        url=urljoin(url,response.headers.get("Location",""));public_url(url);continue
                    if response.status!=200:raise Fault("content_source_unavailable",503)
                    raw=bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw)>maximum:raise Fault("content_too_large",413)
                    return bytes(raw),response.content_type,url,dict(response.headers)
        raise Fault("content_redirect_limit",409)
    async def discover(self,context,body):
        configs=await self.config.list("content_provider");config=next((x for x in configs if x.get("id")==body.get("provider_id")),configs[0] if configs else {})
        async def request(value):
            raw,mime,url,headers=await self.fetch(value["url"],maximum=value.get("max_response_bytes",512*1024))
            return {"status":200,"headers":{"content-type":mime},"body":json.loads(raw) if mime=="application/json" else raw.decode("utf-8")}
        result=await self.discovery.discover(config,body,request=request)
        if body.get("query") and "bilibili" in body.get("sources",["web","bilibili"]):
            try:
                rows=await self.bilibili_search(body["query"],page=body.get("page",1),limit=body.get("limit",8))
                result["candidates"].extend(rows)
                result["sources"].append({"source":"bilibili_search","state":"ok","code":None,"has_more":None,"next_cursor":None})
            except (Fault,aiohttp.ClientError,ValueError,KeyError):result["sources"].append({"source":"bilibili_search","state":"unavailable","code":"source_unavailable","has_more":None,"next_cursor":None})
        result["activity_mode"]=body.get("mode","research");result["seen_state"]="unread_candidates"
        excluded=set(body.get("exclude_keys",[]));result["candidates"]=[x for x in result["candidates"] if x["content_key"] not in excluded and x["candidate_key"] not in excluded]
        return result
    async def bilibili_search(self,query,*,page=1,limit=8):
        from urllib.parse import urlencode
        endpoint="https://api.bilibili.com/x/web-interface/search/type?"+urlencode({"search_type":"video","keyword":query,"page":page,"page_size":min(limit,20)})
        raw,_,_,_=await self.fetch(endpoint,accept="application/json",headers={"Referer":"https://www.bilibili.com/","User-Agent":"Mozilla/5.0"});body=json.loads(raw)
        if body.get("code")!=0:raise Fault("bilibili_search_unavailable",503)
        return [candidate("bilibili","bilibili:"+row["bvid"],kind="video",title=BeautifulSoup(row["title"],"html.parser").get_text(),authors=[row.get("author")],summary=row.get("description", ""),url="https://www.bilibili.com/video/"+row["bvid"],published=row.get("pubdate"),acquired=time.time(),summary_kind="catalog_description") for row in body.get("data",{}).get("result",[])[:limit]]
    async def execute(self,action,attempt):
        await self.authority.check(action.context,action.source_refs)
        url=action.arguments["url"];raw,mime,final,headers=await self.fetch(url)
        if mime=="application/json":
            data=json.loads(raw)
            if "post_stream" in data:content=discourse(data,final)
            else:content={"title":data.get("title"),"text":canonical(data),"posts":[],"next_url":None}
        elif mime in {"text/html","application/xhtml+xml"}:content=html_content(raw,final)
        elif mime.startswith("text/"):content={"title":None,"text":raw.decode("utf-8"),"posts":[],"next_url":None}
        else:raise Fault("content_representation_unsupported",415)
        if not content["text"].strip() and self.browser:
            browser=await self.browser.read(action.context,final)
            raw,mime,content=browser["raw"],browser["mime_type"],browser["content"]
        if not content["text"].strip():raise Fault("content_body_unavailable",503)
        locator={"kind":"forum" if content["posts"] else "webpage","url":final,"requested_url":url,"acquired_at":datetime.now(UTC).isoformat(),"etag":headers.get("Etag"),"last_modified":headers.get("Last-Modified"),"title":content["title"],"authors":content.get("authors",[]),"published_at":content.get("published_at"),"posts":content["posts"]}
        source=await self.sources.capture(action.context,locator=locator,raw=raw,mime_type=mime)
        await self.authority.check(action.context,(source,))
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"succeeded","none","not_requested",coverage=Coverage(content["next_url"] is None,missing=("more_pages",) if content["next_url"] else (),representation="actual_body"),result={"source":wire(source),"content":content,"locator":locator,"activity_mode":action.arguments.get("mode","research"),"read_basis":"actual_source_body"})
    async def read(self,context,source,requested):
        await self.authority.check(context,(source,))
        return await self.sources.read(context,source,requested)
    async def read_source(self,context,body):
        source=SourceRef(**body["source"]);await self.authority.check(context,(source,))
        return await self.sources.read(context,source,body.get("requested",{}))
    async def reconcile(self,action,attempt):
        # Reads may be repeated explicitly as a new revision; they never imply an external write.
        return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"unknown","none","not_requested",error_code="read_receipt_not_available")
    async def cancel(self,action,attempt):return Receipt(str(uuid4()),action.action_id,attempt.attempt_id,"cancelled","none","not_requested")

def html_content(raw,url):
    soup=BeautifulSoup(raw,"html.parser")
    for node in soup.select("script,style,noscript,nav,footer"):node.decompose()
    title=soup.title.get_text(" ",strip=True) if soup.title else None
    posts=[]
    for index,node in enumerate(soup.select("[data-post-id],article.topic-post,.forum-post")):
        author=node.select_one("[data-user-card],.username,.author")
        quote=node.select_one("blockquote")
        posts.append({"id":node.get("data-post-id",str(index+1)),"floor":node.get("data-post-number",index+1),"author":author.get_text(" ",strip=True) if author else None,"text":node.get_text("\n",strip=True),"quote":quote.get_text("\n",strip=True) if quote else None,"edited_at":node.get("data-edited-at")})
    main=soup.select_one("article,main,[role=main]") or soup.body or soup
    next_link=soup.select_one("a[rel=next]");published=soup.select_one("meta[property='article:published_time'],meta[name='date']");author=soup.select_one("meta[name=author]")
    return {"title":title,"text":"\n\n".join(post["text"] for post in posts) if posts else main.get_text("\n",strip=True),"posts":posts,"authors":[author.get("content")] if author else [],"published_at":published.get("content") if published else None,"next_url":urljoin(url,next_link["href"]) if next_link and next_link.get("href") else None}

def discourse(data,url):
    posts=[{"id":str(p["id"]),"floor":p["post_number"],"author":p.get("username"),"text":BeautifulSoup(p.get("cooked",""),"html.parser").get_text("\n",strip=True),"reply_to_floor":p.get("reply_to_post_number"),"created_at":p.get("created_at"),"edited_at":p.get("updated_at")} for p in data["post_stream"].get("posts",[])]
    stream=data["post_stream"].get("stream",[]);loaded={int(p["id"]) for p in posts};remaining=[p for p in stream if p not in loaded]
    return {"title":data.get("title"),"text":"\n\n".join(f"#{p['floor']} {p['author']}\n{p['text']}" for p in posts),"posts":posts,"authors":list(dict.fromkeys(p["author"] for p in posts)),"published_at":data.get("created_at"),"next_url":urljoin(url,"/t/"+str(data["id"])+"/posts.json?post_ids[]="+str(remaining[0])) if remaining else None}
