# clean base image containing only comfyui, comfy-cli and comfyui-manager
FROM runpod/worker-comfyui:5.5.1-base

# install custom nodes into comfyui (first node with --mode remote to fetch updated cache)
RUN comfy node install --exit-on-fail comfyui_essentials --mode remote

# The following custom nodes are listed under unknown_registry but have no aux_id (GitHub repo) provided,
# so they cannot be installed automatically. Keep them here as comments for manual resolution:
# Could not resolve unknown_registry node type: ModelSamplingSD3 (no aux_id provided)
# Could not resolve unknown_registry node type: ModelSamplingSD3 (no aux_id provided)
# Could not resolve unknown_registry node type: INTConstant (no aux_id provided)
# Could not resolve unknown_registry node type: INTConstant (no aux_id provided)
# Could not resolve unknown_registry node type: Seed (rgthree) (no aux_id provided)
# Could not resolve unknown_registry node type: WanImageToVideo (no aux_id provided)
# Could not resolve unknown_registry node type: INTConstant (no aux_id provided)
# Could not resolve unknown_registry node type: wanBlockSwap (no aux_id provided)
# Could not resolve unknown_registry node type: wanBlockSwap (no aux_id provided)
# Could not resolve unknown_registry node type: VHS_VideoCombine (no aux_id provided)
# Could not resolve unknown_registry node type: INTConstant (no aux_id provided)
# Could not resolve unknown_registry node type: VAEDecodeTiled (no aux_id provided)

# download models into comfyui
RUN comfy model download --url https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors --relative-path models/vae --filename wan2.2_vae.safetensors
RUN comfy model download --url https://huggingface.co/FX-FeiHou/wan2.2-Remix/resolve/main/NSFW/Wan2.2_Remix_NSFW_i2v_14b_high_lighting_v2.0.safetensors --relative-path models/diffusion_models --filename Wan2.2_Remix_NSFW_i2v_14b_high_lighting_v2.0.safetensors
RUN comfy model download --url https://huggingface.co/FX-FeiHou/wan2.2-Remix/resolve/main/NSFW/Wan2.2_Remix_NSFW_i2v_14b_low_lighting_v2.0.safetensors --relative-path models/diffusion_models --filename Wan2.2_Remix_NSFW_i2v_14b_low_lighting_v2.0.safetensors
RUN comfy model download --url https://huggingface.co/NSFW-API/NSFW-Wan-UMT5-XXL/resolve/main/nsfw_wan_umt5-xxl_fp8_scaled.safetensors --relative-path models/text_encoders --filename nsfw_wan_umt5-xxl_fp8_scaled.safetensors

# copy all input data (like images or videos) into comfyui (uncomment and adjust if needed)
# COPY input/ /comfyui/input/
