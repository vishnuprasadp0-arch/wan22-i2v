"""
RunPod Serverless Handler — Wan 2.2 Image-to-Video
===================================================

Built to match the app.py / index.html frontend exactly.

How it works:
  1. app.py loads img2vid.json, injects all user values (prompt, seed,
     width, height, fps, etc.) into the workflow nodes, then POSTs:

         { "input": { "workflow": { ...comfyui api workflow... } } }

  2. This handler receives that payload, walks the workflow to find any
     LoadImage node whose 'image' value is a raw base64 string, saves it
     to disk and uploads it to ComfyUI (ComfyUI cannot accept base64
     directly in a workflow — it needs a filename registered via the
     upload API).

  3. The workflow is queued with ComfyUI and polled until done.

  4. The output MP4/WEBP is saved to COMFY_OUTPUT_DIR and the handler
     returns:

         {
             "status":    "completed",
             "video_url": "/outputs/<filename>",   ← served by Flask
             "task_id":   "job_<prompt_id>",
             "seed_used": <int>
         }

     app.py's /api/status/<job_id> route passes this dict straight back
     to the browser, which then sets the <video> src to video_url.

Environment variables (set in RunPod template):
  COMFY_OUTPUT_PATH   Where ComfyUI writes files  (default /comfyui/output)
  COMFY_INPUT_PATH    Where ComfyUI reads input    (default /comfyui/input)

No S3 / external storage needed — Flask serves the file directly.
"""

import base64
import os
import time
import traceback
import uuid
from pathlib import Path

import requests
import runpod

# ── Config ────────────────────────────────────────────────────────────────────
COMFY_HOST       = "http://127.0.0.1:8188"
COMFY_OUTPUT_DIR = Path(os.getenv("COMFY_OUTPUT_PATH", "/comfyui/output"))
COMFY_INPUT_DIR  = Path(os.getenv("COMFY_INPUT_PATH",  "/comfyui/input"))

POLL_INTERVAL = 1.0   # seconds between /history polls
POLL_TIMEOUT  = 600   # seconds before giving up (10 min)
STARTUP_WAIT  = 90    # seconds to wait for ComfyUI on cold start


# ── ComfyUI helpers ────────────────────────────────────────────────────────────

def wait_for_comfy(timeout: int = STARTUP_WAIT) -> None:
    """Block until the ComfyUI HTTP API responds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{COMFY_HOST}/system_stats", timeout=5)
            if r.status_code == 200:
                print("[handler] ✅ ComfyUI is ready")
                return
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    raise RuntimeError(f"ComfyUI did not become ready within {timeout}s")


def upload_image_to_comfy(image_b64: str, filename: str) -> str:
    """
    Write a base64 image to disk and register it with ComfyUI via
    POST /upload/image.  Returns the name ComfyUI stored it under.
    """
    image_bytes = base64.b64decode(image_b64)
    COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = COMFY_INPUT_DIR / filename
    local_path.write_bytes(image_bytes)

    with open(local_path, "rb") as fh:
        resp = requests.post(
            f"{COMFY_HOST}/upload/image",
            files={"image": (filename, fh, "image/png")},
            data={"overwrite": "true"},
            timeout=30,
        )
    resp.raise_for_status()
    registered = resp.json().get("name", filename)
    print(f"[handler] 🖼️  Image uploaded to ComfyUI as '{registered}'")
    return registered


def replace_base64_images_in_workflow(workflow: dict, client_id: str) -> dict:
    """
    Walk every node in the workflow.  For any LoadImage node whose
    'image' input looks like a raw base64 string (not a filename),
    upload it to ComfyUI and replace the value with the registered name.

    app.py sets:  inputs['image'] = image_base64   (a long b64 string)
    ComfyUI needs: inputs['image'] = "some_filename.png"
    """
    for node_id, node in workflow.items():
        if node.get("class_type") != "LoadImage":
            continue

        inputs = node.get("inputs", {})
        image_val = inputs.get("image", "")

        # Detect base64: not a filename (no extension chars in first 50 chars
        # that look like a path), and long enough to be image data.
        if isinstance(image_val, str) and len(image_val) > 256 and "." not in image_val[:50]:
            filename = f"i2v_input_{client_id}.png"
            registered = upload_image_to_comfy(image_val, filename)
            inputs["image"] = registered
            print(f"[handler] 🔄 Node {node_id}: replaced base64 → '{registered}'")

    return workflow


def queue_prompt(workflow: dict, client_id: str) -> str:
    """Submit the workflow to ComfyUI; return prompt_id."""
    payload = {"prompt": workflow, "client_id": client_id}
    resp = requests.post(f"{COMFY_HOST}/prompt", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        details = data.get("node_errors", data["error"])
        raise RuntimeError(f"ComfyUI rejected workflow: {details}")
    prompt_id = data["prompt_id"]
    print(f"[handler] 📬 Queued prompt: {prompt_id}")
    return prompt_id


def poll_until_done(prompt_id: str) -> dict:
    """
    Poll GET /history/<prompt_id> until execution completes.
    Returns the outputs dict from the history entry.
    """
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            r = requests.get(f"{COMFY_HOST}/history/{prompt_id}", timeout=10)
            if r.status_code == 200:
                history = r.json()
                if prompt_id in history:
                    entry = history[prompt_id]
                    status = entry.get("status", {})

                    if status.get("status_str") == "error":
                        msgs = status.get("messages", [])
                        raise RuntimeError(f"ComfyUI execution error: {msgs}")

                    outputs = entry.get("outputs", {})
                    if outputs:
                        print(f"[handler] ✅ Generation complete")
                        return outputs
        except requests.exceptions.RequestException as exc:
            print(f"[handler] ⚠️  Poll error (will retry): {exc}")

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Timed out after {POLL_TIMEOUT}s waiting for prompt {prompt_id}")


def find_output_file(outputs: dict) -> Path:
    """
    Walk ComfyUI's outputs dict and return the path to the first
    video or animated image file (mp4, webp, webm, gif).
    """
    VIDEO_EXTS = {".mp4", ".webp", ".webm", ".gif"}

    for node_output in outputs.values():
        for key in ("gifs", "videos", "images"):
            for item in node_output.get(key, []):
                filename  = item.get("filename", "")
                subfolder = item.get("subfolder", "")
                ext = Path(filename).suffix.lower()
                if ext in VIDEO_EXTS:
                    full_path = COMFY_OUTPUT_DIR / subfolder / filename
                    print(f"[handler] 🎬 Output file: {full_path}")
                    return full_path

    raise FileNotFoundError(f"No video/animated file found in ComfyUI outputs: {outputs}")


def extract_seed_from_workflow(workflow: dict) -> int:
    """Best-effort: pull the seed value used from the workflow."""
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            if key in inputs and isinstance(inputs[key], int):
                return inputs[key]
    return -1


# ── RunPod handler ─────────────────────────────────────────────────────────────

def handler(job: dict) -> dict:
    """
    Main RunPod serverless entry point.

    Expected input shape (matches what app.py sends):
        {
            "input": {
                "workflow": { ...comfyui api-format workflow... }
            }
        }

    Returns:
        {
            "status":    "completed",
            "video_url": "/outputs/<filename>",
            "task_id":   "job_<prompt_id>",
            "seed_used": <int>
        }
    """
    job_input = job.get("input", {})

    # ── Validate ────────────────────────────────────────────────────────────
    workflow = job_input.get("workflow")
    if not workflow or not isinstance(workflow, dict):
        return {"error": "Missing or invalid 'workflow' in input. Expected a ComfyUI API-format workflow dict."}

    client_id = str(uuid.uuid4())

    try:
        # ── Wait for ComfyUI ────────────────────────────────────────────────
        wait_for_comfy()

        # ── Handle base64 images inside LoadImage nodes ─────────────────────
        workflow = replace_base64_images_in_workflow(workflow, client_id)

        # ── Queue and run ────────────────────────────────────────────────────
        seed_used = extract_seed_from_workflow(workflow)
        prompt_id = queue_prompt(workflow, client_id)

        # ── Poll until done ──────────────────────────────────────────────────
        outputs = poll_until_done(prompt_id)

        # ── Locate output file ───────────────────────────────────────────────
        output_path = find_output_file(outputs)

        # video_url is a relative path that Flask's /outputs/<filename> route
        # will serve directly back to the browser.
        video_url = f"/outputs/{output_path.name}"

        return {
            "status":    "completed",
            "video_url": video_url,
            "task_id":   f"job_{prompt_id}",
            "seed_used": seed_used,
        }

    except Exception as exc:
        traceback.print_exc()
        return {"error": str(exc)}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
