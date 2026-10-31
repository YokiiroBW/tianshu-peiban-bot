"""Native engine compilation and transport. Account absence is configuration, not implementation."""
import base64
import io
import json
import zipfile
import httpx
from tianshu.types import OriginKind
from ..external import ProviderError
from .comfy import ComfyUI
from .workflow import Workflow
from .prompt import bound_prompt_values

class Engines:
    @staticmethod
    def compile(profile,intent,workflow=None):
        engine=profile["engine"];parameters=dict(profile.get("parameters",{}))
        description="\n".join(f"{key}: {value}" for key,value in intent.items() if value and key not in {"references","negative_constraints"})
        negative=", ".join(intent.get("negative_constraints",[]))
        if engine=="comfyui":
            if not workflow:raise ProviderError("workflow_required",409)
            definition=Workflow(workflow["graph"],workflow["bindings"],workflow["outputs"])
            semantic={"positive":description,"negative":negative,"character":intent["character"],"outfit":intent["outfit"],"pose":intent["pose"],"background":intent["scene"],"camera":intent["camera"]}
            values=bound_prompt_values(definition,semantic,parameters)
            for index,reference in enumerate(intent.get("references",[])):
                field="reference"+str(index+1) if index else "reference"
                if field not in definition.bindings:raise ProviderError("workflow_reference_binding_missing",409)
                values[field]=reference_name(reference)
            return {"prompt":description,"negative_prompt":negative,"parameters":parameters,"references":intent.get("references",[]),"graph":definition.render(values),"outputs":workflow["outputs"]}
        if engine=="nai":
            allowed={"width","height","scale","sampler","steps","seed","n_samples","noise_schedule","sm","sm_dyn","dynamic_thresholding","cfg_rescale","ucPreset","qualityToggle","v4_prompt","v4_negative_prompt","reference_image_multiple","reference_information_extracted_multiple","reference_strength_multiple","director_reference_images","director_reference_descriptions","director_reference_information_extracted","director_reference_strength_values","director_reference_secondary_strength_values"}
            if set(parameters)-allowed:raise ProviderError("image_parameter_unsupported",400)
            params={"width":832,"height":1216,"steps":28,"scale":5,"sampler":"k_euler_ancestral","n_samples":1,**parameters,"negative_prompt":negative}
            return {"prompt":description,"negative_prompt":negative,"parameters":params,"references":intent.get("references",[]),"request":{"input":description,"model":profile["model"],"action":"generate","parameters":params}}
        if engine=="gpt_image":
            allowed={"size","quality","background","output_format","output_compression","n","moderation","input_fidelity"}
            if set(parameters)-allowed:raise ProviderError("image_parameter_unsupported",400)
            return {"prompt":description+("\nAvoid: "+negative if negative else ""),"negative_prompt":negative,"parameters":parameters,"references":intent.get("references",[])}
        raise ProviderError("image_engine_unknown",400)
    @staticmethod
    async def gpt(profile,token,compiled,references):
        endpoint=(profile.get("endpoint") or "https://api.openai.com/v1").rstrip("/")
        params={"model":profile["model"],"prompt":compiled["prompt"],**compiled["parameters"]}
        async with httpx.AsyncClient(timeout=600,follow_redirects=False) as client:
            if references:
                files=[("image[]",(f"reference-{i}.png",raw,mime)) for i,(raw,mime) in enumerate(references)]
                response=await image_request(client,endpoint+"/images/edits",headers={"Authorization":"Bearer "+token},data={key:str(value) for key,value in params.items()},files=files)
            else:
                params.pop("input_fidelity",None)
                response=await image_request(client,endpoint+"/images/generations",headers={"Authorization":"Bearer "+token},json=params)
            check(response);body=response.json();images=[]
            for item in body.get("data",[]):
                if not item.get("b64_json"):raise ProviderError("image_result_missing",502,effect="unknown")
                images.append((base64.b64decode(item["b64_json"],validate=True),"image/"+params.get("output_format","png")))
            if not images:raise ProviderError("image_result_missing",502,effect="unknown")
            return images,body.get("usage")
    @staticmethod
    async def nai(profile,token,compiled,references,origin):
        if origin!=OriginKind.HUMAN:raise ProviderError("nai_human_trigger_required",403)
        request=dict(compiled["request"]);params=dict(request["parameters"])
        if references:
            mode=profile.get("reference_mode")
            if mode=="vibe":params["reference_image_multiple"]=[base64.b64encode(raw).decode() for raw,_ in references]
            elif mode=="precise":params["director_reference_images"]=[base64.b64encode(raw).decode() for raw,_ in references]
            elif mode=="img2img":request["action"]="img2img";params["image"]=base64.b64encode(references[0][0]).decode()
            else:raise ProviderError("nai_reference_mode_required",409)
        request["parameters"]=params
        endpoint=(profile.get("endpoint") or "https://image.novelai.net").rstrip("/")
        async with httpx.AsyncClient(timeout=600,follow_redirects=False) as client:
            response=await image_request(client,endpoint+"/ai/generate-image",headers={"Authorization":"Bearer "+token,"Accept":"application/zip"},json=request)
            check(response);raw=response.content;images=[]
            if raw.startswith(b"PK"):
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    if len(archive.infolist())>64 or sum(x.file_size for x in archive.infolist())>128*1024*1024:raise ProviderError("image_artifact_too_large",413,effect="unknown")
                    for entry in archive.infolist():
                        if entry.is_dir():continue
                        if entry.file_size>64*1024*1024:raise ProviderError("image_artifact_too_large",413,effect="unknown")
                        if entry.filename.lower().endswith((".png",".webp",".jpg")):
                            images.append((archive.read(entry),"image/png" if entry.filename.lower().endswith(".png") else "image/webp" if entry.filename.lower().endswith(".webp") else "image/jpeg"))
            else:
                body=response.json()
                for item in body.get("images",[]):images.append((base64.b64decode(item.get("image",item) if isinstance(item,dict) else item,validate=True),"image/png"))
            if not images:raise ProviderError("image_result_missing",502,effect="unknown")
            return images,None

async def image_request(client,url,**kwargs):
    async with client.stream("POST",url,**kwargs) as response:
        check(response)
        data=bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data)>192*1024*1024:raise ProviderError("image_response_budget",413,effect="unknown")
        return httpx.Response(response.status_code,headers=response.headers,content=bytes(data),request=response.request)

def check(response):
    if response.status_code in {400,401,402,403,404,413,422,429}:raise ProviderError("image_rejected_"+str(response.status_code),409)
    if response.status_code not in {200,201}:raise ProviderError("image_outcome_unknown",effect="unknown")

def reference_name(reference):
    from ..contracts import digest
    return "tianshu_"+digest(reference)+{"image/jpeg":".jpg","image/webp":".webp"}.get(reference.get("mime_type"),".png")
