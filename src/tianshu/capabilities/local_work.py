"""Wait for complete local filesystem work before releasing its real resource ownership."""
import asyncio

async def settled(local,operation,*args,**kwargs):
    task=asyncio.create_task(local(operation,*args,**kwargs));cancelled=None
    while True:
        try:result=await asyncio.shield(task);break
        except asyncio.CancelledError as error:
            if task.cancelled():raise
            cancelled=cancelled or error
        except Exception:
            if cancelled:raise cancelled from None
            raise
    if cancelled:raise cancelled
    return result
