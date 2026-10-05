# Image backend v1

Companion owns image jobs, workflow analysis and compilation. Platform reuses actor management authorization and the existing encrypted credential catalog. Only ComfyUI is implemented in this release.

POST /internal/v1/image-backend/read, /manage and /compile use schema definitions read_request, manage_request, compile_request and response. Workflow analysis and compilation never submit image jobs; assist_model can execute the configured language model. Existing life-runtime/v2 image.request remains the sole image generation operation.

connection.configure uses connection version; workflow.select, bindings.update and actor.configure use the actor profile version. workflow.analyze checks actor version but does not advance it. Workflow version is a content hash. Uncertain widget serialization must not be guessed. Preserve graph links, fixed style and LoRA parameters; validate model output against actual node candidates.

This is the implementation baseline, not a real GPU/QQ acceptance record.
